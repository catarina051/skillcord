# Skillcord Runtime V1 — Agent Instructions

## Source of truth

Before making changes, read:

1. `docs/superpowers/specs/2026-08-11-skillcord-runtime-design.md`
2. `docs/superpowers/plans/2026-08-11-skillcord-runtime-v1-implementation-plan.md`

The specification defines WHAT must be built.
The implementation plan defines HOW the V1 should be implemented.

## Scope

Implement V1 AI Runtime only.

Do NOT implement:
- Environment Inspector V1.1
- Environment Bootstrapper V2
- OS package installation
- winget/apt/brew/dnf/pacman integration
- LLM or embedding-based overlap detection
- external model/API calls
- provider hooks or arbitrary scripts
- Humanizer text execution
- Open Design daemon/MCP execution

`Skillcord` is an internal codename only.

## Development workflow

Use Superpowers methodology.

Follow the implementation plan task-by-task and in order.

For each task:
1. read the complete task;
2. write the failing test first;
3. run it and confirm the expected failure;
4. implement the minimum required change;
5. run the task-specific tests;
6. run relevant regression tests;
7. review the diff;
8. commit the task separately.

Do not combine multiple implementation-plan tasks into one commit.

Do not silently modify requirements or architecture.

If the implementation plan and specification conflict, STOP and report
the conflict instead of guessing.

## Security

Security boundaries in the specification are mandatory.

Provider discovery must not:
- execute hooks;
- execute setup scripts;
- install software;
- make model/API calls;
- modify the host development environment.

Unknown or malformed state must fail closed where required by the spec.

## Generated files

User-authored content outside Skillcord managed blocks must be preserved
byte-for-byte.

Never overwrite arbitrary user-authored content.

## Verification

Never claim a task is complete without running its verification commands.

At the end of every task report:
- files changed;
- tests executed;
- test results;
- commit hash;
- remaining risks or uncertainties.
