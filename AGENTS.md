# Skillcord Runtime V1 - Agent Instructions

## Source of truth

Before changing the V1 runtime, read:

1. `docs/superpowers/specs/2026-08-11-skillcord-runtime-design.md`
2. `docs/superpowers/plans/2026-08-11-skillcord-runtime-v1-implementation-plan.md`

The specification defines WHAT must be built. The implementation plan defines
HOW V1 should be implemented. If they conflict, STOP and report the conflict
instead of guessing.

For resumed SDD work, read the plan once per controller session, then use the
task brief, report, review package, ledger, and compact resume artifact. Do not
reread the complete plan for every task.

## Current checkpoint

Implementation is paused. Do not resume it until the user explicitly asks.

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

## Priority and Superpowers override

Use this priority order:

1. correctness
2. maintainability
3. token efficiency
4. wall-clock speed

The strict SDD orchestration policy below overrides the default Superpowers SDD
workflow and any earlier project workflow instruction where they conflict.

## Model policy

- Default controller: `gpt-5.6-terra`, reasoning `medium`.
- Default subagent: `gpt-5.6-terra`, reasoning `medium`.
- When available in the current spawn allowlist, use Luna/medium for mechanical
  edits, small scoped rereviews, straightforward tests, repetitive changes,
  simple documentation, and simple file-level tasks.
- Use Terra/medium for normal implementation, review, integration, and most
  debugging.
- Terra/high requires a concrete documented reason.
- Sol/high is allowed only for architecture/design decisions with meaningful
  uncertainty, security-sensitive code, subtle concurrency or transactional
  correctness, a confirmed blocker Terra failed to solve, or a final
  whole-branch review whose diff is genuinely complex.
- Sol/max is forbidden unless the user explicitly authorizes it.
- Task size alone never justifies model escalation.
- Record every Sol use in the SDD ledger exactly as
  `MODEL ESCALATION: <reason Sol was necessary>`.

## SDD dispatch and review loop

Only the controller may dispatch agents. Implementers and reviewers must not
spawn reviewers, helpers, testers, investigators, or any other subagent. A
worker spawning a worker is an orchestration defect.

Every SDD spawn must use `fork_turns = "none"`. Supply context through task
briefs, reports, review packages, ledger entries, and explicit interface notes.

The normal per-task sequence is:

1. one implementation execution;
2. one task-diff review;
3. if clean, complete the task;
4. if a real Critical/Important issue exists, resume the same implementer for
   one targeted fix;
5. perform one scoped rereview of the original finding, fix diff, and new
   breakage caused by that fix.

If that rereview still has a Critical/Important issue, stop the automatic fix
loop. Do not start rounds 2-5. The controller must perform one triage:

- reviewer wrong: adjudicate and record the ruling;
- minor or non-load-bearing: defer it to final review;
- task/spec/architecture wrong: perform one architecture diagnosis;
- implementer genuinely failed: escalate once to a stronger agent.

The normal maximum is four executions per task: implementer, reviewer, one fix
on the same implementer, and one scoped rereviewer. Do not exceed it without a
ledgered reason. Never recreate the old five-round review loop.

Task reviewers review only the task diff. Scoped rereviewers review only the
original finding, the fix diff, and breakage introduced by that fix. Unrelated
observations become deferred/minor ledger entries and cannot extend the loop.

Perform exactly one whole-branch final review. If it finds issues, use one
combined fix dispatch and one scoped rereview. Do not create per-finding agents
or a second automatic fix wave. Adjudicate or defer residual non-load-bearing
findings.

## Waiting policy

Eliminate short polling. Never repeat `wait_agent` with 30- or 60-second
timeouts.

After dispatch, do useful controller bookkeeping if available. Otherwise use
the longest practical event-driven bounded wait supported by the runtime,
targeting 5-10 minutes. Completion events should be consumed immediately. If a
long wait times out, reconcile agent state once, then use another long wait if
needed. Target at most two wait calls per normal agent execution. Record a
ledger warning when an execution causes more than three wait calls.

## Controller context and role

The controller coordinates dispatch, rulings, ledger state,
dependency/interface context, escalation, and final integration. It does not
implement task code or repeat file inspection/tests already handled by a child
unless necessary to resolve a finding.

Do not paste full diffs, reports, test logs, git logs, plans, or previous-task
summaries into controller conversation context. Store them in the plan's SDD
workspace. A completed child returns only:

```text
STATUS: DONE | BLOCKED | CONCERNS
REPORT: <path>
COMMIT: <hash>
TESTS: <one-line result>
CONCERN: <one line or NONE>
```

Normal child final responses should be about 150 words or fewer. Do not reread
complete reports unless needed to resolve a finding.

If controller context exceeds approximately 100k tokens, or compaction occurs
after substantial SDD work, write a clean handoff in this plan's SDD workspace
and start a fresh session instead of continuing indefinitely. The handoff must
contain only plan path, branch/worktree, completed tasks and commits, current
task, important rulings, unresolved blockers, interfaces needed by future
tasks, and next action. Keep it under 1,500 tokens. The ledger remains the
authoritative history; do not copy prior reports into the handoff.

## Test budget

Tests are evidence-driven. A normal implementer runs:

1. the relevant failing/focal test;
2. the relevant test after the fix;
3. relevant regression tests;
4. the full task-level suite once when ready.

Do not repeat the same test command without a relevant state change. A reviewer
reads recorded evidence and does not rerun the full suite by default. It may run
a test only when evidence is missing or suspicious, or a concrete finding needs
verification. A rereviewer verifies only the fix and affected regression
surface. Run the complete project suite once near final branch verification,
not after every small correction.

## Tool-call circuit breaker

- At about 30 tool calls on a normal task, assess whether work is converging.
- At about 50 tool calls without convergence, stop and reassess.
- At about 60 tool calls on a complex task, explicitly justify continuing.
- Do not reach 100+ calls through repeated searches or tests without a
  documented reason.
- Repeating identical or nearly identical commands is prohibited unless state
  changed.

## Batching and task boundaries

When two or more adjacent tasks are small, same-shaped, independent, and touch
compatible files/interfaces, use one implementation dispatch and one review.
Do not batch work that needs separate correctness judgment. Batched tasks still
receive separate task commits unless the user or plan explicitly says
otherwise.

For each task or approved batch:

1. read the complete task brief;
2. write the failing test first;
3. run it and confirm the expected failure;
4. implement the minimum required change;
5. run task-specific tests and relevant regression tests within the test budget;
6. review the diff;
7. commit each plan task separately.

Do not silently modify requirements or architecture. Do not refactor unrelated
code, update unrelated documentation, clean neighboring modules, add speculative
abstractions, or broaden tests beyond the relevant surface unless correctness
requires it.

## Token/runtime ledger

For every execution, record available telemetry:

- role
- task
- model
- reasoning effort
- input tokens
- cached input
- output tokens
- runtime
- tool calls
- test calls
- review round

Flag and reassess rather than automatically continuing:

- `TOKEN WARNING:` more than 2M tokens for a routine execution;
- `TOOL WARNING:` more than 50 tools for a routine task;
- `REVIEW WARNING:` a task reaches a second failed rereview;
- `MODEL WARNING:` Sol was used without an explicit escalation reason.

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

Preserve user-authored content outside Skillcord managed blocks byte-for-byte.
Never overwrite arbitrary user-authored content.

## Verification and reporting

Never claim a task is complete without running its verification commands.

At the end of every task report, record:

- files changed;
- tests executed and results;
- commit hash;
- remaining risks or uncertainties.
