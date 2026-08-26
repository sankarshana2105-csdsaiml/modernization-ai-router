# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose credentials, source repositories, tenant data, or command-execution boundaries. Use GitHub's private vulnerability reporting feature for this repository.

Include the affected version, reproduction steps using non-sensitive sample data, impact, and any suggested mitigation. Do not include real provider keys or customer source code.

## Security boundaries

- Provider secrets must come from environment variables or a deployment secret manager.
- External providers are not approved for proprietary source code by default.
- Generated code must execute through the sandbox worker, never directly on the application host.
- The default sandbox has no network, no host secrets, no Docker socket, no Linux capabilities, a read-only root filesystem, and strict resource/time limits.
- Production images should be pinned by digest and scanned before use.
- Multi-worker deployments must use shared quota and accounting stores.

The project does not support quota evasion, account farming, credential pooling, or bypassing provider terms.
