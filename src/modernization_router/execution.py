from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger("modernization_router.execution")
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ERROR_WORDS = re.compile(r"(?i)(error|failed|failure|exception|traceback|panic|fatal)")


class ExecutionError(Exception):
    """Base execution-worker error."""


class UnsafeExecutionRequestError(ExecutionError):
    """The command or workspace violates the sandbox policy."""


class SandboxUnavailableError(ExecutionError):
    """The configured container runtime is unavailable."""


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    workspace: Path
    runtime: str
    command: tuple[str, ...]
    timeout_seconds: float = 120.0
    environment: Mapping[str, str] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    request_id: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    def diagnostic_summary(self, *, max_chars: int = 8_000) -> str:
        """Deterministically compact tool output before sending it back to any model."""
        combined = "\n".join(part for part in (self.stdout, self.stderr) if part).strip()
        if not combined:
            return f"exit_code={self.exit_code}; timed_out={self.timed_out}; no output"
        lines = combined.splitlines()
        relevant = [line for line in lines if _ERROR_WORDS.search(line)]
        tail = lines[-40:]
        selected = []
        seen: set[str] = set()
        for line in [*relevant[:40], *tail]:
            if line not in seen:
                selected.append(line)
                seen.add(line)
        header = f"exit_code={self.exit_code}; timed_out={self.timed_out}\n"
        summary = header + "\n".join(selected)
        return summary[:max_chars]


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    workspace_root: Path
    images: Mapping[str, str]
    allowed_commands: Mapping[str, frozenset[str]]
    max_timeout_seconds: float = 300.0
    max_output_bytes: int = 200_000
    memory_limit: str = "1g"
    cpu_limit: float = 1.0
    pids_limit: int = 256
    container_user: str = "65532:65532"
    allowed_environment: frozenset[str] = frozenset({"CI", "NO_COLOR", "PYTHONUNBUFFERED"})

    def validate(self, request: ExecutionRequest) -> Path:
        try:
            root = self.workspace_root.resolve(strict=True)
            workspace = request.workspace.resolve(strict=True)
        except FileNotFoundError as exc:
            raise UnsafeExecutionRequestError("Execution workspace does not exist") from exc
        if not workspace.is_dir():
            raise UnsafeExecutionRequestError("Execution workspace must be a directory")
        if workspace == root or root not in workspace.parents:
            raise UnsafeExecutionRequestError(
                "Execution workspace must be a job directory inside the configured root"
            )
        if request.runtime not in self.images:
            raise UnsafeExecutionRequestError("Runtime is not allowlisted")
        if not request.command:
            raise UnsafeExecutionRequestError("Command cannot be empty")
        executable = request.command[0]
        if executable != Path(executable).name:
            raise UnsafeExecutionRequestError("Executable paths are not allowed")
        if executable not in self.allowed_commands.get(request.runtime, frozenset()):
            raise UnsafeExecutionRequestError("Command is not allowlisted for this runtime")
        if request.timeout_seconds <= 0 or request.timeout_seconds > self.max_timeout_seconds:
            raise UnsafeExecutionRequestError("Requested timeout exceeds sandbox policy")
        if any("\x00" in argument for argument in request.command):
            raise UnsafeExecutionRequestError("Command arguments contain invalid characters")
        disallowed_environment = request.environment.keys() - self.allowed_environment
        if disallowed_environment:
            raise UnsafeExecutionRequestError(
                f"Environment variables are not allowlisted: {sorted(disallowed_environment)}"
            )
        if any("\x00" in value for value in request.environment.values()):
            raise UnsafeExecutionRequestError("Environment contains invalid characters")
        return workspace


def default_sandbox_policy(workspace_root: Path) -> SandboxPolicy:
    """Safe baseline. Production should pin image digests after vulnerability review."""
    return SandboxPolicy(
        workspace_root=workspace_root,
        images={
            "python": "python:3.12-slim",
            "node": "node:22-slim",
            "java": "eclipse-temurin:21-jdk",
            "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",
            "go": "golang:1.24",
            "rust": "rust:1.85-slim",
        },
        allowed_commands={
            "python": frozenset({"python", "pytest", "ruff"}),
            "node": frozenset({"node", "npm", "npx", "pnpm"}),
            "java": frozenset({"java", "javac", "mvn", "gradle"}),
            "dotnet": frozenset({"dotnet"}),
            "go": frozenset({"go"}),
            "rust": frozenset({"cargo", "rustc"}),
        },
    )


class SandboxExecutor(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


ProcessFactory = Callable[..., Awaitable[Any]]


class DockerSandboxExecutor:
    """Runs untrusted jobs in tightly restricted, disposable Docker containers."""

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        docker_executable: str = "docker",
        process_factory: ProcessFactory = asyncio.create_subprocess_exec,
    ) -> None:
        self.policy = policy
        self.docker_executable = docker_executable
        self._process_factory = process_factory

    def available(self) -> bool:
        return shutil.which(self.docker_executable) is not None

    @staticmethod
    def _container_name(request_id: str) -> str:
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:20]
        return f"modernization-job-{digest}"

    def _docker_command(self, request: ExecutionRequest, workspace: Path) -> tuple[str, ...]:
        container_name = self._container_name(request.request_id)
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.policy.pids_limit),
            "--memory",
            self.policy.memory_limit,
            "--cpus",
            str(self.policy.cpu_limit),
            "--user",
            self.policy.container_user,
            "--workdir",
            "/workspace",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=128m",
            "--env",
            "HOME=/tmp",
            "--volume",
            f"{workspace}:/workspace:rw",
        ]
        for key, value in sorted(request.environment.items()):
            command.extend(("--env", f"{key}={value}"))
        command.append(self.policy.images[request.runtime])
        command.extend(request.command)
        return tuple(command)

    async def _read_bounded(
        self, stream: asyncio.StreamReader | None, limit: int
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        retained = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(16_384)
            if not chunk:
                break
            remaining = limit - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return bytes(retained), truncated

    async def _force_remove_container(self, container_name: str) -> None:
        cleanup = await self._process_factory(
            self.docker_executable,
            "rm",
            "--force",
            "--",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(cleanup.wait(), timeout=10.0)
        except TimeoutError:
            cleanup.kill()
            await cleanup.wait()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        workspace = self.policy.validate(request)
        if not self.available() and self._process_factory is asyncio.create_subprocess_exec:
            raise SandboxUnavailableError("Docker is not installed or not available on PATH")

        docker_command = self._docker_command(request, workspace)
        container_name = self._container_name(request.request_id)
        logger.info(
            "sandbox_execution_started",
            extra={
                "request_id": request.request_id,
                "runtime": request.runtime,
                "executable": request.command[0],
            },
        )
        started = time.perf_counter()
        process = await self._process_factory(
            *docker_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(
            self._read_bounded(process.stdout, self.policy.max_output_bytes)
        )
        stderr_task = asyncio.create_task(
            self._read_bounded(process.stderr, self.policy.max_output_bytes)
        )
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=request.timeout_seconds)
        except TimeoutError:
            timed_out = True
            await self._force_remove_container(container_name)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        stdout_data, stdout_truncated = await stdout_task
        stderr_data, stderr_truncated = await stderr_task
        duration_ms = (time.perf_counter() - started) * 1_000
        result = ExecutionResult(
            request_id=request.request_id,
            exit_code=None if timed_out else process.returncode,
            stdout=_ANSI_ESCAPE.sub("", stdout_data.decode("utf-8", errors="replace")),
            stderr=_ANSI_ESCAPE.sub("", stderr_data.decode("utf-8", errors="replace")),
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
        )
        logger.info(
            "sandbox_execution_finished",
            extra={
                "request_id": request.request_id,
                "runtime": request.runtime,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": round(duration_ms, 2),
                "output_truncated": result.output_truncated,
            },
        )
        return result


@dataclass(frozen=True, slots=True)
class VerificationReport:
    results: tuple[ExecutionResult, ...]

    @property
    def succeeded(self) -> bool:
        return bool(self.results) and all(result.succeeded for result in self.results)

    def diagnostics_for_ai(self, *, max_chars: int = 12_000) -> str:
        summaries = [result.diagnostic_summary() for result in self.results if not result.succeeded]
        return "\n\n".join(summaries)[:max_chars]


class VerificationWorker:
    """Runs trusted pipeline commands in order and stops at the first failure."""

    def __init__(self, executor: SandboxExecutor) -> None:
        self.executor = executor

    async def verify(self, requests: Sequence[ExecutionRequest]) -> VerificationReport:
        results: list[ExecutionResult] = []
        for request in requests:
            result = await self.executor.execute(request)
            results.append(result)
            if not result.succeeded:
                break
        return VerificationReport(results=tuple(results))
