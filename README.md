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
- A protected browser beta that accepts pasted code or up to 50 local project files / 180,000 source characters and separates generated code from migration notes
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

The repository includes a FastAPI entrypoint for Vercel. The public-beta cloud configuration now supports ordered free-provider failover:

1. OpenRouter free router
2. Gemini free tier
3. Groq free tier
4. Cerebras free tier

A provider participates only when its official environment credential is available in the deployment. Paid OpenAI remains disabled by default. No provider credential is committed to GitHub.

Cloud endpoints:

- `GET /` - browser-based legacy-code modernization screen
- `GET /healthz` - public liveness and configuration status
- `GET /docs` - OpenAPI interface documentation
- `POST /v1/route` - protected model-routing endpoint

Set a long random `ROUTER_ACCESS_KEY` in the Vercel project's Production environment before enabling AI requests. Clients send it as a bearer credential.

The included cloud models are deliberately not approved for proprietary code. A `proprietary` or `local_only` request therefore fails closed until the owner reviews provider data terms and explicitly changes the policy. The Docker execution worker is also not exposed by this serverless API; run it on a dedicated container host with the documented isolation controls.

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
