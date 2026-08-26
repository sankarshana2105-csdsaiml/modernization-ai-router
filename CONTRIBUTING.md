# Contributing

Contributions are welcome through issues and pull requests.

## Development

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
python -m build
```

Tests must not call live model providers or consume credits. Use `FakeProviderAdapter` or an in-memory HTTP transport.

Provider integrations must use documented authentication and API surfaces. Do not submit credential rotation, account farming, scraped browser sessions, quota bypasses, or other mechanisms that violate provider terms.

Any change to privacy routing or command execution must include tests that prove the restrictive behavior remains enforced.
