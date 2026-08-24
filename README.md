# Skillcord

> Internal codename for a local-first AI development runtime prototype.

Skillcord explores how teams can make AI coding-agent capabilities reproducible across
projects and harnesses. It discovers locally available providers, normalizes their skill
metadata, identifies deterministic capability overlap, records explicit human decisions,
pins resolved content, and generates safe project-level configuration.

The V1 runtime is under active development. It is not a published package or a reserved
public CLI brand.

## Why this project exists

Developers often combine multiple coding agents and skill collections. Each provider has
its own layout, metadata, update behavior, and integration model. Capabilities may overlap,
while project instructions can drift between Claude Code, Codex, and generic
`AGENTS.md`-compatible tools.

Skillcord provides one local, inspectable layer for composing those capabilities without
silently choosing on the user's behalf or modifying the host development environment.

## V1 capabilities

The current runtime core includes:

- explicit discovery adapters for Superpowers, Everything Claude Code, Open Design, and
  Humanizer;
- normalized provider and skill records with stable, provider-scoped identifiers;
- deterministic overlap grouping based on names, declared capabilities, registry aliases,
  and existing overrides;
- persistent user decisions with missing-reference validation;
- immutable Git revision resolution, content hashing, lock generation, and drift checks;
- byte-preserving managed blocks for generated instructions;
- shared `AGENTS.md` policy generation with isolated Claude Code and Codex adapters;
- previewable sync plans, atomic application, rollback-safe failure handling, and ownership
  checks;
- append-only audit history for Skillcord-managed sync changes;
- structured doctor checks and deterministic machine-readable status.

The CLI orchestration, golden end-to-end fixtures, CI safety gate, and final release
documentation remain planned work. See the
[implementation plan](docs/superpowers/plans/2026-08-11-skillcord-runtime-v1-implementation-plan.md)
for the full sequence.

## Safety model

V1 deliberately operates within a narrow boundary:

- no operating-system package installation;
- no `winget`, `apt`, `brew`, `dnf`, or `pacman` orchestration;
- no provider hook or setup-script execution during discovery;
- no external model, embedding, or API calls for overlap detection;
- no Humanizer text execution;
- no Open Design daemon or MCP execution;
- no silent suppression of unresolved capabilities: ordinary unresolved groups default to
  **keep all**;
- no best-effort repair of malformed managed state;
- no overwrite of user-authored content outside Skillcord-managed blocks.

Unknown or unsafe state fails closed where required by the V1 specification.

## Supported providers

| Provider | V1 support boundary |
| --- | --- |
| Superpowers | Discovers declarative engineering workflow skills and records resolved content. |
| Everything Claude Code | Discovers supported declarative assets and reports executable hooks as inactive, partial support. |
| Open Design | Discovers declarative skills only; external daemon and MCP runtimes are never launched. |
| Humanizer | Discovers and configures an optional harness-only capability; Skillcord never sends prose to a model. |

## How the runtime fits together

```text
Provider sources
      |
      v
Discovery -> Normalization -> Deterministic overlap groups
                                      |
                                      v
                              Explicit decisions
                                      |
                                      v
                          Locking and content hashes
                                      |
                                      v
                  Adapter plans -> Diff -> Safe sync
                                      |
                                      v
                              Doctor and status
```

The implementation is split into focused modules under `src/skillcord/`:

```text
adapters/        harness-specific artifact planning
decisions/       explicit override persistence and validation
discovery/       provider and harness discovery
locking/         revisions, hashes, locks, and drift detection
managed_blocks/ byte-preserving ownership boundaries
models/          canonical Pydantic domain models
normalization/   stable identifiers
overlap/         deterministic grouping and resolution
providers/       provider-specific discovery adapters
status/          doctor checks and JSON serialization
sync/            diff planning and safe application
```

## Local development

Requirements:

- Python 3.11 or newer;
- a virtual environment;
- development dependencies from `pyproject.toml`.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Run the verification suite:

```bash
python -m ruff check .
python -m mypy src/skillcord
python -m pytest
```

Tests are hermetic: they use local fixtures and must not install host packages, execute
provider hooks, or require model/API access.

## Design documents

- [V1 design specification](docs/superpowers/specs/2026-08-11-skillcord-runtime-design.md)
- [V1 implementation plan](docs/superpowers/plans/2026-08-11-skillcord-runtime-v1-implementation-plan.md)

These documents are the source of truth for scope, security boundaries, acceptance
criteria, and task order.

## Project status

This repository is a development prototype. The `Skillcord` name must be re-evaluated for
package, repository, CLI, web, and trademark collisions before any public release.
