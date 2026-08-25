# V1 security boundaries

V1 manages AI development configuration and capability composition only. It does not manage
the operating system or a development environment.

- It never installs or updates OS packages and never orchestrates `winget`, `apt`, `brew`,
  `dnf`, or `pacman`.
- It never performs privilege elevation, production access, secret retrieval/generation, or
  arbitrary provider hook/setup-script execution.
- Provider discovery, overlap grouping, sync planning, and tests require no network, model,
  embedding, or external API calls.
- Humanizer is configuration-only and harness-only; V1 never sends text to Humanizer or a
  model. Open Design support is declarative-only; no daemon or MCP execution occurs.
- Provider content is untrusted until explicitly selected. Required executable installation
  hooks are reported as unsupported/partial rather than executed.
- Generated `AGENTS.md` content is owned only between `<!-- skillcord:begin -->` and
  `<!-- skillcord:end -->`. Content outside that managed block is preserved byte-for-byte.
  Malformed, nested, or duplicate markers fail closed.

Every generated change is previewable through `diff` before mutation. Unsafe, malformed, or
non-deterministic state fails closed where the V1 policy requires it.
