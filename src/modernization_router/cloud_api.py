from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import build_router, load_config
from .exceptions import AllModelsFailedError, NoEligibleModelsError
from .models import ChatMessage, PrivacyMode, TaskType
from .service import ModernizationAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "router.cloud.toml"
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskType
    messages: list[MessageInput] = Field(min_length=1, max_length=30)
    privacy: PrivacyMode = PrivacyMode.PUBLIC
    allow_premium_fallback: bool = False
    estimated_input_tokens: int = Field(default=2_000, ge=1, le=500_000)
    max_output_tokens: int = Field(default=2_000, ge=1, le=16_000)
    required_capabilities: set[str] = Field(default_factory=set, max_length=10)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("required_capabilities")
    @classmethod
    def validate_capabilities(cls, values: set[str]) -> set[str]:
        if any(len(value) > 80 for value in values):
            raise ValueError("Capability names must be 80 characters or fewer")
        return values

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 20:
            raise ValueError("Metadata is limited to 20 fields")
        if any(len(key) > 80 or len(value) > 500 for key, value in values.items()):
            raise ValueError("Metadata keys or values exceed the allowed length")
        return values


class AttemptOutput(BaseModel):
    provider_id: str
    model_id: str
    attempt_number: int
    outcome: str
    latency_ms: float
    error_type: str | None = None


class UsageOutput(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


class RouteOutput(BaseModel):
    request_id: str
    provider_id: str
    model_id: str
    content: str
    usage: UsageOutput
    attempts: list[AttemptOutput]
    used_premium_fallback: bool


def _gateway_configured() -> bool:
    return bool(os.getenv("AI_GATEWAY_API_KEY") or os.getenv("VERCEL_OIDC_TOKEN"))


@lru_cache(maxsize=1)
def get_ai_service() -> ModernizationAI:
    config_path = Path(os.getenv("ROUTER_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    return ModernizationAI(build_router(load_config(config_path)))


def require_access_key(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = os.getenv("ROUTER_ACCESS_KEY")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The API owner has not configured ROUTER_ACCESS_KEY",
        )

    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid bearer access key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


app = FastAPI(
    title="Modernization AI Router",
    version="0.2.0",
    description="Protected cloud API for policy-aware modernization model routing.",
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/", response_class=FileResponse)
async def root() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "gateway_identity_available": _gateway_configured(),
        "route_access_key_configured": bool(os.getenv("ROUTER_ACCESS_KEY")),
    }


@app.post(
    "/v1/route",
    response_model=RouteOutput,
    dependencies=[Depends(require_access_key)],
)
async def route_request(
    request: RouteInput,
    ai: Annotated[ModernizationAI, Depends(get_ai_service)],
) -> RouteOutput:
    try:
        result = await ai.run(
            request.task,
            [
                ChatMessage(role=message.role, content=message.content)
                for message in request.messages
            ],
            privacy=request.privacy,
            allow_premium_fallback=request.allow_premium_fallback,
            estimated_input_tokens=request.estimated_input_tokens,
            max_output_tokens=request.max_output_tokens,
            required_capabilities=frozenset(request.required_capabilities),
            metadata=request.metadata,
        )
    except NoEligibleModelsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AllModelsFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="All eligible models are temporarily unavailable or out of quota",
        ) from exc

    return RouteOutput(
        request_id=result.request_id,
        provider_id=result.provider_id,
        model_id=result.model_id,
        content=result.content,
        usage=UsageOutput(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            cost_usd=result.usage.cost_usd,
        ),
        attempts=[
            AttemptOutput.model_validate(attempt, from_attributes=True)
            for attempt in result.attempts
        ],
        used_premium_fallback=result.used_premium_fallback,
    )
