# Development rules

Use Ponytail `full` principles for coding work in this repository.

- Understand the affected flow before editing it, then make the smallest correct change.
- Reuse existing code, the Python standard library, and installed dependencies before adding code or packages.
- Do not add speculative abstractions, wrappers, configuration, files, or features.
- Prefer straightforward, descriptive code over clever one-liners. Minimal must remain easy to read.
- Keep privacy controls, validation, security, quota enforcement, retries, error handling, and useful logging.
- Add the smallest focused test that proves non-trivial behavior and failure handling.
- Before committing, review the diff for code or dependencies that can be removed without weakening correctness.

When minimalism conflicts with an explicit requirement, the requirement wins.
