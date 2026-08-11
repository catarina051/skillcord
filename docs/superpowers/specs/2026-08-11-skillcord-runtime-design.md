# Skillcord — Universal AI Development Runtime

**Status:** Revised design specification for approval
**Date:** 2026-08-11
**Scope:** V1 only — AI provider/skill runtime. No machine mutation.

## 1. Product vision

Skillcord is an open-source, local-first runtime for making AI coding-agent capabilities reproducible across projects and agent harnesses.

Its first release solves one focused problem: a developer may use Claude Code, Codex, generic `AGENTS.md`-compatible agents, and multiple external skill/provider collections at the same time, but those collections overlap, evolve independently, and are configured differently per harness. Skillcord discovers what is installed, normalizes capability metadata, detects likely overlap, records explicit human decisions, pins exact provider content, and emits agent-specific configuration from one project-level source of intent.

V1 does **not** install programming runtimes, package managers, IDEs, databases, Docker, or operating-system dependencies. Those capabilities belong to later products/specifications.

## 2. Working name and namespace policy

Working product name: **Skillcord**.
Working Python distribution and CLI name: `skillcord`.

The name was selected after checking current public search results for obvious PyPI/GitHub collisions on 2026-08-11. No exact competing package surfaced in that search. This is not a namespace reservation.

Before the first public release, maintainers must re-check:

- PyPI project namespace;
- GitHub repository/org naming;
- common CLI command collisions in package indexes and developer tooling;
- basic web/trademark collision risk.

If an exact collision appears before release, rename before publishing rather than shipping an alias that conflicts with an established CLI.

## 3. V1 goals

V1 must:

1. discover supported AI providers and skills from local/project sources;
2. normalize provider/skill metadata into a common internal model;
3. detect likely capability overlap without relying on a permanently hand-curated global ownership table;
4. ask the user to resolve material ambiguity;
5. store only explicit overrides and prior user decisions;
6. pin provider state by immutable source revision plus applied-content hashes;
7. generate a canonical `AGENTS.md` and minimal harness-specific adapters;
8. support Claude Code and Codex as first-class adapters;
9. support generic `AGENTS.md`-compatible agents without bespoke integration;
10. integrate Superpowers, Everything Claude Code, Open Design, and Humanizer as initial providers;
11. expose machine-readable status suitable for automation;
12. support deterministic non-interactive sync when every required decision is already declared;
13. never mutate the operating-system development environment.

## 4. Non-goals

V1 does not:

- install Node, Python, PHP, Java, Flutter, Docker, XAMPP, databases, IDEs, or system packages;
- orchestrate `winget`, `apt`, `dnf`, `pacman`, or `brew`;
- infer or enforce programming-runtime version compatibility;
- execute arbitrary repository setup scripts;
- deploy applications;
- access production systems;
- retrieve or invent secrets;
- implement multi-agent swarm orchestration;
- guarantee semantic equivalence between every third-party skill format;
- automatically decide every provider conflict.

## 5. Product principles

### 5.1 Human authority over ambiguity

A recommendation is not permission. When two capabilities materially overlap and Skillcord cannot resolve them from an existing explicit decision, it must show the evidence, recommend an option when possible, and ask the user.

### 5.2 Derived facts over duplicated facts

Project manifests remain authoritative for software/runtime requirements. Skillcord's project configuration stores only AI-development intent, provider choices, capability preferences, adapter choices, and overrides that ordinary project manifests cannot express.

### 5.3 Reproducibility means immutable source + content

A provider is not considered pinned merely because a human-readable version label exists. V1 pins:

- provider source/repository identity;
- immutable commit/revision when available;
- hash of each applied skill/config artifact;
- Skillcord schema version.

### 5.4 Discovery first, curation second

Skillcord discovers capabilities from provider/skill metadata. The registry does not attempt to permanently own a universal mapping such as `tdd -> superpowers`.

The registry may provide heuristics and known aliases, but persistent conflict decisions live as project/user overrides.

### 5.5 Local-first and inspectable

Generated configuration, provider pins, overlap decisions, and status must be inspectable as ordinary text/structured files.

## 6. Supported harnesses

First-class V1 adapters:

- Claude Code;
- OpenAI Codex;
- generic `AGENTS.md`-compatible agents.

Designed for later adapters:

- Gemini CLI;
- Cursor;
- OpenCode;
- Windsurf;
- other Agent Skills-compatible harnesses.

`AGENTS.md` is the universal project instruction surface. Harness-specific files contain only adapter-specific details.

## 7. Initial providers

V1 ships provider definitions for:

- **Superpowers** — engineering workflow/process skills;
- **Everything Claude Code (ECC)** — specialist skills, review/security capabilities, and supporting assets;
- **Open Design** — UI/UX/design capabilities;
- **Humanizer (`blader/humanizer`)** — human-facing prose refinement.

Provider inclusion does not imply that every skill from a provider is activated.

## 8. High-level architecture

```text
Skillcord
│
├── Discovery
│   ├── provider discovery
│   ├── skill discovery
│   └── harness discovery
│
├── Normalization
│   ├── metadata parser
│   ├── capability descriptors
│   └── source/content fingerprints
│
├── Overlap Engine
│   ├── exact/alias matching
│   ├── metadata similarity
│   ├── known heuristics
│   └── unresolved-conflict reporting
│
├── Decision Store
│   ├── project overrides
│   └── optional user defaults
│
├── Locking
│   ├── source revision pins
│   └── content hashes
│
├── Adapters
│   ├── AGENTS.md
│   ├── Claude Code
│   └── Codex
│
├── Writing Policy
│   └── contextual Humanizer activation
│
└── CLI
    ├── init
    ├── scan
    ├── conflicts
    ├── sync
    ├── doctor
    ├── diff
    ├── status
    └── writing
```

## 9. Repository structure

```text
skillcord/
├── src/skillcord/
│   ├── cli/
│   ├── discovery/
│   ├── models/
│   ├── normalization/
│   ├── overlap/
│   ├── decisions/
│   ├── locking/
│   ├── adapters/
│   ├── providers/
│   ├── writing/
│   └── status/
├── registry/
│   ├── providers/
│   ├── aliases/
│   └── adapter-capabilities/
├── templates/
│   ├── AGENTS.md.j2
│   └── adapters/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
├── pyproject.toml
├── LICENSE
└── README.md
```

Implementation language: **Python**.

Expected installation once published:

```bash
pipx install skillcord
```

## 10. Project-side files

A configured project may contain:

```text
.ai/
├── project.yaml
├── ai-lock.yaml
└── overrides.yaml

AGENTS.md
CLAUDE.md          # only when Claude adapter is enabled
```

### 10.1 `.ai/project.yaml`

Contains AI-specific project intent only.

```yaml
schema_version: 1

project:
  intent: fullstack_web_application

ai:
  harnesses:
    - claude
    - codex

  desired_capabilities:
    - planning
    - testing
    - debugging
    - code_review
    - security
    - human_writing

writing:
  mode: contextual
```

It must not duplicate runtime constraints already present in `package.json`, `composer.json`, `pyproject.toml`, `pubspec.yaml`, or equivalent manifests.

### 10.2 `.ai/overrides.yaml`

Stores explicit conflict decisions rather than a universal hand-maintained capability map.

```yaml
schema_version: 1

overrides:
  tdd:
    prefer: superpowers.test-driven-development
    suppress:
      - ecc.tdd

  debugging:
    prefer: superpowers.systematic-debugging
```

An override may be created by the user directly or after an interactive conflict-resolution decision.

### 10.3 `.ai/ai-lock.yaml`

Pins exactly what was applied.

```yaml
schema_version: 1
skillcord_schema_version: 1

providers:
  superpowers:
    source: github
    repository: obra/superpowers
    revision: "<immutable-commit-sha>"
    artifacts:
      brainstorming:
        path: skills/brainstorming/SKILL.md
        sha256: "<content-hash>"
      systematic-debugging:
        path: skills/systematic-debugging/SKILL.md
        sha256: "<content-hash>"
```

V1 must reject lock entries that contain only mutable labels when an immutable revision/content hash can be recorded.

## 11. Skill/provider discovery model

For each discovered skill, Skillcord normalizes at least:

```text
provider_id
skill_id
name
description
source_path
source_revision
content_hash
triggers/tags when available
harness compatibility when declared
```

Discovery may use provider manifests, `SKILL.md` frontmatter, known provider directory conventions, and adapter metadata.

Missing metadata must remain unknown. Skillcord must not invent unsupported claims about a skill.

## 12. Overlap detection

Overlap detection is advisory and evidence-based.

Signals may include:

1. normalized/exact capability names;
2. provider-declared aliases/tags;
3. description similarity;
4. trigger similarity;
5. known registry aliases;
6. explicit project overrides.

Output must distinguish:

- **exact duplicate/known conflict**;
- **likely overlap**;
- **possible overlap**;
- **no detected overlap**.

Skillcord must not silently suppress a provider based solely on low-confidence semantic similarity.

Example:

```text
Likely overlap detected: test-driven development

A. superpowers.test-driven-development
B. ecc.tdd

Evidence:
- normalized names match known aliases
- descriptions both require test-first implementation

Recommendation: A
Confidence: high
Reason: project already selected Superpowers as process methodology.

Choose [A/B/keep both/ignore]:
```

The answer is persisted as an override.

## 13. Humanizer writing policy

Humanizer is a content capability, not an engineering-process owner.

Default contextual behavior:

```text
source code / config / schemas / API references
→ OFF

technical internal docs
→ OFF by default

public README / user-facing docs
→ ON, light

report / article
→ ON with required formality preserved

email / social / presentation copy
→ ON
```

Modes:

- `natural`;
- `professional`;
- `academic`;
- `concise`;
- `preserve_voice`.

The writing pass must preserve:

- factual meaning;
- code blocks and commands;
- identifiers;
- citations and quoted evidence;
- structured data;
- required terminology.

It must not simulate humanity by deliberately adding typos, slang, unsupported anecdotes, or fabricated personal experience.

Optional style profiles may store derived preferences rather than source writing samples.

## 14. CLI

### `skillcord init`

Creates/updates AI-specific project configuration without touching the machine development environment.

It may:

- detect harnesses;
- detect existing skill/provider directories;
- ask which harnesses should be supported;
- create `.ai/project.yaml`;
- generate an initial scan proposal.

### `skillcord scan`

Discovers providers/skills and produces normalized inventory.

Read-only by default.

### `skillcord conflicts`

Shows unresolved/known overlaps and supports interactive resolution.

### `skillcord sync`

Materializes the already-approved provider/adapter state into the project/harness configuration.

Interactive mode asks before overwriting existing generated files.

Non-interactive mode is allowed only when all necessary decisions already exist in project configuration/overrides and no destructive/ambiguous overwrite is required.

### `skillcord sync --non-interactive`

Deterministic automation path for CI/onboarding. It must fail closed on unresolved ambiguity.

### `skillcord doctor`

Verifies AI-runtime readiness only:

- project schema validity;
- requested harness availability/configuration;
- provider source presence;
- lock/hash consistency;
- unresolved conflicts;
- generated adapter drift;
- Humanizer policy validity.

### `skillcord diff`

Shows proposed/generated changes before sync or provider update.

### `skillcord status --json`

Emits machine-readable status.

### `skillcord writing humanize <path>`

Runs the writing provider on an eligible text file and shows a diff before applying changes unless an explicit already-approved non-interactive policy permits otherwise.

## 15. Machine-readable status

Minimal schema:

```json
{
  "schema_version": 1,
  "timestamp": "2026-08-11T17:00:00Z",
  "ready": false,
  "checks": [
    {
      "id": "lock.integrity",
      "status": "pass",
      "details": {
        "providers_checked": 4
      }
    },
    {
      "id": "conflicts.unresolved",
      "status": "fail",
      "details": {
        "count": 1
      }
    }
  ]
}
```

Every check must have a stable `id`, status, and optional structured details. Aggregate `ready` is derived from check severity, not separately asserted without evidence.

## 16. Update policy

Provider update flow:

```text
fetch metadata/revision information
        ↓
compare immutable revision + content hashes
        ↓
show changed skills/artifacts
        ↓
re-run overlap analysis for affected capabilities
        ↓
show diff
        ↓
user approval or pre-approved deterministic policy
        ↓
sync
        ↓
write new lock
```

An update must not silently change a previously resolved capability owner.

## 17. History and audit

V1 records Skillcord-managed changes to generated AI configuration.

Command:

```bash
skillcord history
```

Each entry should state:

- timestamp;
- command/action;
- files changed;
- previous/new provider revision when applicable;
- whether a deterministic inverse operation exists.

V1 does not claim universal rollback.

## 18. Security boundaries

V1 is intentionally low-risk:

- no OS package installation;
- no privilege elevation;
- no production access;
- no secret retrieval/generation;
- no arbitrary provider script execution merely for discovery;
- no `curl | bash` installation path executed by Skillcord;
- provider Markdown/content is treated as untrusted input until explicitly selected/synced;
- generated files are shown through diff when they would replace user-authored content.

If a provider requires executable installation hooks, V1 reports that requirement rather than executing the hook automatically.

## 19. Testing strategy

Required test categories:

### Unit

- provider metadata parsing;
- skill normalization;
- hash calculation;
- lock serialization/validation;
- alias normalization;
- overlap scoring/classification;
- override precedence;
- writing-policy eligibility;
- status aggregation.

### Integration

- Superpowers fixture discovery;
- ECC fixture discovery;
- Open Design fixture discovery;
- Humanizer fixture discovery;
- known TDD overlap resolution;
- lock drift after fixture modification;
- Claude adapter generation;
- Codex adapter generation;
- generic `AGENTS.md` generation;
- non-interactive sync fails on unresolved conflict.

### Golden-file

Generated `AGENTS.md`, adapter files, `ai-lock.yaml`, and status JSON are compared against reviewed fixtures.

No test should require modifying host OS packages.

## 20. Acceptance criteria

V1 is successful when:

1. A project can initialize Skillcord without changing the OS development environment.
2. Skillcord can discover fixture versions of all four initial providers.
3. Discovered skills are normalized into stable identifiers and content hashes.
4. A known overlap between two providers is surfaced without silent suppression.
5. A user conflict decision is persisted and reused on the next scan.
6. Low-confidence overlap does not automatically disable a skill.
7. `ai-lock.yaml` pins immutable source revision plus applied-content hashes.
8. Modifying a locked artifact produces detectable drift.
9. `project.yaml` contains no duplicated Node/PHP/Python/etc. version requirements derived from ordinary manifests.
10. Claude Code and Codex can share universal policy through generated `AGENTS.md` while retaining isolated adapter-specific configuration.
11. `skillcord sync --non-interactive` succeeds only when all decisions are deterministic and fails closed otherwise.
12. `status --json` includes schema version, timestamp, per-check results, structured details, and a derived readiness result.
13. Humanizer is enabled for eligible human-facing prose and excluded from protected code/config/citation content.
14. Provider updates cannot silently replace an explicit capability-owner override.
15. All V1 automated tests run without installing or updating host-machine development packages.

## 21. Deferred roadmap

### V1.1 — Development Environment Inspector

Read-only discovery of project/machine requirements. See separate design specification.

### V2 — Development Environment Bootstrapper

Approval-gated environment mutation via trusted package managers. See separate design specification.

## 22. Final V1 boundary

Skillcord V1 manages **AI development configuration and capability composition**.

It does not manage the operating system. This narrow boundary is deliberate: V1 proves the novel provider/skill conflict-resolution and reproducibility model before adding cross-platform environment mutation.
