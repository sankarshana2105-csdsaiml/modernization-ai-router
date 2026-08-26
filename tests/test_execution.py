from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modernization_router.execution import (
    DockerSandboxExecutor,
    ExecutionRequest,
    ExecutionResult,
    UnsafeExecutionRequestError,
    VerificationWorker,
    default_sandbox_policy,
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"tests passed\n",
        stderr: bytes = b"",
        returncode: int = 0,
        block: bool = False,
    ) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode
        self.block = block
        self.killed = False

    async def wait(self) -> int:
        if self.block and not self.killed:
            await asyncio.sleep(60)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class RecordingFactory:
    def __init__(self, main: FakeProcess) -> None:
        self.main = main
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, **_: object) -> FakeProcess:
        self.calls.append(tuple(args))
        if len(args) > 1 and args[1] == "rm":
            return FakeProcess()
        return self.main


def job_workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "jobs"
    workspace = root / "job-123"
    workspace.mkdir(parents=True)
    return root, workspace


@pytest.mark.asyncio
async def test_docker_executor_applies_strict_isolation_flags(tmp_path: Path) -> None:
    root, workspace = job_workspace(tmp_path)
    factory = RecordingFactory(FakeProcess())
    executor = DockerSandboxExecutor(
        default_sandbox_policy(root),
        process_factory=factory,
    )

    result = await executor.execute(
        ExecutionRequest(
            workspace=workspace,
            runtime="python",
            command=("python", "-m", "pytest"),
            environment={"CI": "true"},
        )
    )

    command = factory.calls[0]
    assert result.succeeded
    assert ("--network", "none") == command[
        command.index("--network") : command.index("--network") + 2
    ]
    assert "--read-only" in command
    assert ("--cap-drop", "ALL") == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in command
    assert "HOME=/tmp" in command
    assert f"{workspace.resolve()}:/workspace:rw" in command
    assert "/var/run/docker.sock" not in " ".join(command)


def test_policy_rejects_workspace_outside_controlled_root(tmp_path: Path) -> None:
    root, _ = job_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = default_sandbox_policy(root)

    with pytest.raises(UnsafeExecutionRequestError):
        policy.validate(
            ExecutionRequest(
                workspace=outside,
                runtime="python",
                command=("python", "script.py"),
            )
        )


def test_policy_rejects_unapproved_command_and_secret_environment(tmp_path: Path) -> None:
    root, workspace = job_workspace(tmp_path)
    policy = default_sandbox_policy(root)

    with pytest.raises(UnsafeExecutionRequestError):
        policy.validate(
            ExecutionRequest(
                workspace=workspace,
                runtime="python",
                command=("powershell", "-Command", "Get-ChildItem"),
            )
        )
    with pytest.raises(UnsafeExecutionRequestError):
        policy.validate(
            ExecutionRequest(
                workspace=workspace,
                runtime="python",
                command=("python", "script.py"),
                environment={"DATABASE_URL": "must-not-enter-sandbox"},
            )
        )


@pytest.mark.asyncio
async def test_timeout_force_removes_container(tmp_path: Path) -> None:
    root, workspace = job_workspace(tmp_path)
    main = FakeProcess(block=True)
    factory = RecordingFactory(main)
    executor = DockerSandboxExecutor(
        default_sandbox_policy(root),
        process_factory=factory,
    )

    result = await executor.execute(
        ExecutionRequest(
            workspace=workspace,
            runtime="python",
            command=("python", "slow.py"),
            timeout_seconds=0.01,
        )
    )

    assert result.timed_out is True
    assert result.exit_code is None
    assert any(len(call) > 2 and call[1:3] == ("rm", "--force") for call in factory.calls)


def test_diagnostic_summary_keeps_errors_and_bounds_size() -> None:
    result = ExecutionResult(
        request_id="test",
        exit_code=1,
        stdout="\n".join(f"normal line {index}" for index in range(100)),
        stderr="ERROR: assertion failed\nTraceback: sample",
        duration_ms=1.0,
    )

    summary = result.diagnostic_summary(max_chars=500)

    assert "ERROR: assertion failed" in summary
    assert "Traceback: sample" in summary
    assert len(summary) <= 500


@pytest.mark.asyncio
async def test_verification_worker_stops_after_first_failure() -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(
                request_id=request.request_id,
                exit_code=1,
                stdout="",
                stderr="test failed",
                duration_ms=1.0,
            )

    executor = FakeExecutor()
    worker = VerificationWorker(executor)
    requests = [
        ExecutionRequest(workspace=Path("unused"), runtime="python", command=("pytest",)),
        ExecutionRequest(workspace=Path("unused"), runtime="python", command=("ruff", "check")),
    ]

    report = await worker.verify(requests)

    assert report.succeeded is False
    assert executor.calls == 1
    assert "test failed" in report.diagnostics_for_ai()
