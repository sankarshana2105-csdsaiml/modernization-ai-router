# Modernization AI Router

A production-oriented Python routing and verification layer for legacy-code modernization. The SaaS calls one internal AI interface while this package selects an eligible model, enforces privacy and budget policy, automatically fails over, and runs generated code through a restricted container rather than on the application host.

No live provider request is made by the test suite. No OpenAI key or paid account is required to build or test this package.

## What is implemented

- Provider adapters:
  - OpenAI-compatible HTTP APIs, including local Ollama, OpenRouter, Groq, Cerebras, Cloudflare Workers AI, GitHub Models, NVIDIA NIM, and OpenAI
  - Official Gemini `generateContent` API
  - Deterministic fake provider for tests and local simulations
- Capability registry for `code_analysis`, `refactoring`, `test_generation`, `architecture`, and `debugging`
- Free/cheap-first model ordering with configurable priorities and premium fallback
- Per-model requests-per-minute/day, tokens-per-minute/day, and daily-cost limits
- Atomic in-process quota reservations so concurrent jobs cannot all claim the last slot
- Automatic failover on quota exhaustion, rate limits, authentication failures, timeouts, and provider failures
- Exponential backoff with jitter for genuinely transient failures
- Provider circuit breakers and active health checks
- Structured JSON logs containing operational metadata but never prompt/source content
- Input-token, output-token, and USD accounting
- `PUBLIC`, `PROPRIETARY`, and `LOCAL_ONLY` privacy modes
- Environment-variable-only secrets
- A SaaS-facing `ModernizationAI` facade
- A Docker execution worker with an allowlisted runtime/command policy
- No-network containers with a read-only root, dropped capabilities, non-root user, process/memory/CPU limits, bounded output, and forced timeout cleanup
- Deterministic test/compiler-output compaction before diagnostics are sent back to a model

The router explicitly does **not** rotate accounts or credentials, evade quotas, farm accounts, or bypass provider limits or terms.

## Routing flow

```text
Modernization SaaS worker
        |
        v
ModernizationAI.run(...)
        |
        v
Capability + privacy policy
        |
        v
Free -> cheap -> standard -> premium
        |
        v
Quota reservation -> circuit check -> bounded provider call
        |                              |
        | success                      | failure
        v                              v
Usage/cost ledger                 retry or fail over
        |
        v
Restricted verification container
        |
        +-- success -> return verified result
        |
        +-- failure -> compact diagnostics -> debugging route
```

## Safe defaults

The example configuration enables local Ollama and OpenRouter free routing. Groq, Gemini, Cloudflare Workers AI, Cerebras, GitHub Models, NVIDIA, and OpenAI premium are present but disabled until an operator deliberately enables them and provides an official key.

External providers are **not** approved for proprietary code by default. A model is eligible for `PROPRIETARY` jobs only when it is local or an operator explicitly sets `approved_for_proprietary = true` after reviewing the provider contract, retention, training, regional-processing, and commercial-use terms.

OpenRouter's free catalog is useful for development and graceful degradation, but its published free limit is small and it does not provide a production SLA. Local inference should be the zero-cost private baseline; a paid provider can later be enabled as a tightly capped fallback.

## Quick start

Requirements: Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

Docker is optional for router-only development and required for isolated command execution. The application never starts untrusted code directly on the host.

Copy `config/router.example.toml` to a deployment-specific file and change provider/model IDs and limits. Put secrets in server environment variables or a secret manager; never place them in the TOML file.

For a local zero-cost model, install Ollama separately and make the configured model available. The sample expects an OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1`.

## Protected cloud API

The repository includes a FastAPI entrypoint for Vercel. The cloud configuration uses an `AI_GATEWAY_API_KEY` when supplied and falls back to Vercel's automatically injected OIDC token. No provider credential is committed to GitHub.

Cloud endpoints:

- `GET /` - browser-based legacy-code modernization screen
- `GET /healthz` - public liveness and configuration status
- `GET /docs` - OpenAPI interface documentation
- `POST /v1/route` - protected model-routing endpoint

Set a long random `ROUTER_ACCESS_KEY` in the Vercel project's Production environment before enabling AI requests. Clients send it as a bearer credential:

```powershell
$headers = @{ Authorization = "Bearer $env:ROUTER_ACCESS_KEY" }
$body = @{
  task = "code_analysis"
  messages = @(@{ role = "user"; content = "Inspect this public example" })
  privacy = "public"
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Uri "https://YOUR-DEPLOYMENT.vercel.app/v1/route" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

The included cloud models are deliberately not approved for proprietary code. A `proprietary` or `local_only` request therefore fails closed until the owner reviews provider data terms and explicitly changes the policy. The Docker execution worker is also not exposed by this serverless API; run it on a dedicated container host with the documented isolation controls.

## SaaS integration

```python
from modernization_router import (
    ChatMessage,
    ModernizationAI,
    PrivacyMode,
    TaskType,
    build_router,
    load_config,
)

config = load_config("config/router.toml")
router = build_router(config)
ai = ModernizationAI(router)

result = await ai.run(
    TaskType.REFACTORING,
    [ChatMessage(role="user", content=job_prompt)],
    privacy=PrivacyMode.PROPRIETARY,
    allow_premium_fallback=True,
    estimated_input_tokens=estimated_tokens,
    max_output_tokens=8_000,
    metadata={"job_id": job_id, "tenant_id": tenant_id},
)

converted_code = result.content
selected_model = result.model_id
cost = result.usage.cost_usd
```

The FastAPI/controller layer should enqueue modernization work. A worker then calls `ModernizationAI`; controllers do not need provider-specific logic.

## Isolated code and command execution

The command runner accepts only a controlled job directory, an allowlisted runtime, and an allowlisted executable. Arguments are passed directly to Docker without a shell. The default policy supports Python, Node.js, Java, .NET, Go, and Rust build/test commands.

```python
from pathlib import Path

from modernization_router import (
    DockerSandboxExecutor,
    ExecutionRequest,
    VerificationWorker,
    default_sandbox_policy,
)

jobs_root = Path("/srv/modernization/jobs")
policy = default_sandbox_policy(jobs_root)
executor = DockerSandboxExecutor(policy)
verification = VerificationWorker(executor)

report = await verification.verify(
    [
        ExecutionRequest(
            workspace=jobs_root / job_id,
            runtime="python",
            command=("python", "-m", "pytest", "-q"),
            timeout_seconds=120,
            environment={"CI": "true"},
        ),
        ExecutionRequest(
            workspace=jobs_root / job_id,
            runtime="python",
            command=("ruff", "check", "."),
            timeout_seconds=60,
        ),
    ]
)

if not report.succeeded:
    compact_diagnostics = report.diagnostics_for_ai()
```

Runtime images use readable tags in the example. Production deployments should replace them with reviewed image digests and preinstall dependencies so sandbox networking can remain disabled.

Compilers, tests, and formatters consume no model tokens. Only compact failure diagnostics need to return to a debugging model, allowing local/free inference to handle routine repair loops.

## Priority and fallback configuration

Models are sorted by:

1. cost tier (`free`, `cheap`, `standard`, `premium`)
2. numeric `priority` (lower first)
3. estimated request cost
4. stable model ID

Premium models are considered only after every eligible lower-cost model is unavailable or fails. A job can prohibit premium usage with `allow_premium_fallback=False`. Provider credentials alone never enable a disabled provider.

## Quotas, health, and persistence

`InMemoryQuotaTracker` is concurrency-safe within one Python process and is appropriate for local development or a single worker. Before horizontally scaling, implement the same reservation operations in Redis so every worker shares rate and daily limits.

`UsageLedger` is the accounting boundary. Persist its `UsageRecord` fields to the SaaS database for tenant billing, budgets, audit history, and job-level cost reporting.

`await router.health()` returns provider checks, circuit states, quota counters, and usage totals. Expose that result only through an authenticated internal operations endpoint; it should not be a public API.

## Tests proving automatic continuation

The test suite covers:

- locally tracked daily quota exhaustion
- provider-reported quota exhaustion
- provider outage failover
- retry and recovery on transient errors
- timeout cancellation and failover
- proprietary-code exclusion from unapproved providers
- task-capability filtering
- premium fallback and per-job premium prohibition
- token/cost accounting
- request/response compatibility for OpenAI-style and Gemini adapters
- sandbox workspace and command-policy enforcement
- container isolation flags and absence of Docker-socket access
- timeout cleanup, bounded diagnostics, and stop-on-first-failure verification

All provider behavior is simulated or uses in-memory HTTP transports, so tests cannot consume credits.

## Production checklist

- Replace in-memory quota state with Redis before adding multiple workers.
- Persist usage records to Postgres and attach tenant/job IDs.
- Configure exact current model pricing before enabling any paid model.
- Enforce tenant and global spend caps independently of provider-side caps.
- Review and record provider privacy approval before setting `approved_for_proprietary`.
- Put provider keys in the deployment secret manager and rotate them normally; never rotate to bypass limits.
- Add provider-specific contract tests in staging using a tiny, non-proprietary prompt.
- Pin production model IDs where reproducibility matters and monitor provider deprecations.
- Keep raw repository text out of logs, traces, metrics labels, and error messages.
- Run the worker on dedicated infrastructure with no sensitive host mounts.
- Pin and scan sandbox images before production deployment.

## License

Apache License 2.0. See `LICENSE`.
