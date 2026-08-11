# Skillcord Runtime V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the V1 local-first AI provider/skill runtime that discovers four supported providers, normalizes skills, detects deterministic capability overlap, persists human decisions, pins resolved content, manages project AI configuration safely, and generates reproducible Claude Code/Codex/AGENTS.md integration without mutating the host development environment.

**Architecture:** Implement a Python 3.11+ CLI around a small domain model. Provider-specific adapters convert heterogeneous local provider layouts into normalized `SkillRecord`/`ProviderSnapshot` objects; deterministic overlap grouping and explicit overrides resolve composition; a lock service fingerprints the exact resolved artifacts; rendering and managed-block services materialize only Skillcord-owned configuration. The CLI remains orchestration-only: no LLM/model calls, no OS package installation, no arbitrary hook/script execution, and no network requirement for tests.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, PyYAML, Jinja2, pytest, pytest-cov, Ruff, mypy, standard-library `hashlib`, `pathlib`, `difflib`, `subprocess` (read-only Git metadata only).

## Global Constraints

- Scope is V1 only: AI provider/skill runtime; no machine/environment bootstrapper work.
- `Skillcord` is an internal codename only. Do not publish a PyPI package, reserve a public CLI brand, or encode branding assumptions outside the Python module during V1 implementation.
- V1 must never install Node, Python, PHP, Java, Flutter, Docker, IDEs, databases, or OS packages.
- V1 must never execute arbitrary provider hooks/scripts during discovery or sync.
- Overlap detection is fully local and deterministic: normalized name, explicit registry alias, provider-declared capability/tag, or existing override only. No embeddings, LLMs, remote APIs, description similarity, or trigger similarity.
- Unresolved overlap groups default to **keep all**. No skill is silently suppressed.
- Interactive conflict prompts are grouped by capability, with a default budget of 10 groups per session.
- Humanizer is discovery/pinning/harness configuration only. Skillcord itself never sends prose to a model/API and never auto-applies Humanizer to README, CHANGELOG, technical docs, code, commands, citations, or structured data.
- `AGENTS.md` user content outside `<!-- skillcord:begin -->` / `<!-- skillcord:end -->` must be preserved byte-for-byte.
- Malformed, nested, or duplicate managed markers must fail closed.
- `project.yaml` contains AI intent/config only and must not duplicate runtime versions from ordinary manifests.
- Locking pins the content actually resolved by the harness/provider using immutable revisions when available plus SHA-256 hashes of applied artifacts.
- `doctor` must detect drift caused by edits, harness updates, or provider-managed updates.
- Overrides reference normalized skill IDs; if a referenced ID disappears after an update/rename, `doctor` fails explicitly.
- ECC executable hooks are not run by Skillcord; partial support must be visible in status/doctor output.
- Open Design V1 support is declarative-only; do not launch/install its daemon or MCP runtime.
- `sync --non-interactive` must fail closed on malformed managed blocks, missing override references, non-deterministic overwrites, or unresolved single-owner policy; ordinary overlap groups remain deterministic via keep-all.
- All automated tests must run without installing/updating host packages and without network/model calls.
- Every user-visible generated artifact must be previewable through `diff` before mutation.

---

## Planned File Map

```text
pyproject.toml
README.md
LICENSE
src/skillcord/
├── __init__.py
├── cli/
│   ├── app.py
│   └── rendering.py
├── models/
│   ├── capability.py
│   ├── config.py
│   ├── provider.py
│   ├── lock.py
│   └── status.py
├── providers/
│   ├── base.py
│   ├── generic.py
│   ├── superpowers.py
│   ├── ecc.py
│   ├── open_design.py
│   └── humanizer.py
├── discovery/
│   ├── harnesses.py
│   ├── sources.py
│   └── service.py
├── overlap/
│   ├── aliases.py
│   ├── grouping.py
│   └── resolution.py
├── decisions/
│   ├── store.py
│   └── validation.py
├── locking/
│   ├── hashing.py
│   ├── revisions.py
│   └── service.py
├── managed_blocks/
│   ├── parser.py
│   └── writer.py
├── adapters/
│   ├── base.py
│   ├── agents_md.py
│   ├── claude.py
│   └── codex.py
├── sync/
│   ├── planner.py
│   └── applier.py
├── status/
│   ├── checks.py
│   ├── doctor.py
│   └── serializer.py
└── history/
    └── store.py
registry/
├── providers/
│   ├── superpowers.yaml
│   ├── ecc.yaml
│   ├── open_design.yaml
│   └── humanizer.yaml
└── aliases/
    └── capabilities.yaml
templates/
├── agents_managed.md.j2
├── claude_managed.md.j2
└── humanizer_policy.md.j2
tests/
├── unit/
├── integration/
├── golden/
└── fixtures/providers/
    ├── superpowers/
    ├── ecc/
    ├── open_design/
    └── humanizer/
```

---

### Task 1: Bootstrap the Python package and canonical schema models

**Files:**
- Create: `pyproject.toml`
- Create: `src/skillcord/__init__.py`
- Create: `src/skillcord/models/config.py`
- Create: `src/skillcord/models/provider.py`
- Create: `src/skillcord/models/capability.py`
- Create: `src/skillcord/models/lock.py`
- Create: `src/skillcord/models/status.py`
- Create: `tests/unit/models/test_config.py`
- Create: `tests/unit/models/test_provider.py`
- Create: `tests/unit/models/test_status.py`

**Interfaces:**
- Produces: `ProjectConfig`, `OverrideConfig`, `SkillRecord`, `ProviderComponent`, `ProviderSnapshot`, `CapabilityGroup`, `LockFile`, `StatusReport`, `CheckResult`.
- Later tasks must import these types rather than creating duplicate dict schemas.

- [ ] **Step 1: Add package/test tooling configuration**

Create `pyproject.toml` with local codename-only metadata and no publishing step:

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "skillcord-internal"
version = "0.0.0"
description = "Internal codename package for the V1 AI runtime prototype"
requires-python = ">=3.11"
dependencies = [
  "jinja2>=3.1,<4",
  "pydantic>=2.8,<3",
  "pyyaml>=6.0,<7",
  "typer>=0.12,<1",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.11,<2",
  "pytest>=8.3,<9",
  "pytest-cov>=5,<6",
  "ruff>=0.6,<1",
]

[project.scripts]
skillcord-dev = "skillcord.cli.app:app"

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["skillcord"]
```

- [ ] **Step 2: Write failing model tests**

Create tests that encode the spec boundaries:

```python
# tests/unit/models/test_config.py
import pytest
from pydantic import ValidationError
from skillcord.models.config import ProjectConfig


def test_project_config_accepts_ai_only_fields() -> None:
    cfg = ProjectConfig.model_validate(
        {
            "schema_version": 1,
            "project": {"intent": "fullstack_web_application"},
            "ai": {
                "harnesses": ["claude", "codex"],
                "desired_capabilities": ["planning", "security"],
            },
        }
    )
    assert cfg.ai.harnesses == ["claude", "codex"]


def test_project_config_rejects_runtime_requirements() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(
            {
                "schema_version": 1,
                "project": {"intent": "api"},
                "ai": {"harnesses": ["claude"], "desired_capabilities": []},
                "requirements": {"node": ">=20"},
            }
        )
```

```python
# tests/unit/models/test_provider.py
from pathlib import Path
from skillcord.models.provider import SkillRecord


def test_skill_record_has_stable_provider_scoped_id() -> None:
    record = SkillRecord(
        provider_id="superpowers",
        skill_id="test-driven-development",
        name="Test Driven Development",
        description="Write the test first.",
        source_path=Path("/tmp/SKILL.md"),
        content_hash="a" * 64,
        capabilities={"tdd"},
        harnesses={"claude", "codex"},
    )
    assert record.normalized_id == "superpowers.test-driven-development"
```

```python
# tests/unit/models/test_status.py
from skillcord.models.status import CheckResult, CheckStatus, StatusReport


def test_ready_is_derived_from_checks() -> None:
    report = StatusReport.from_checks(
        [
            CheckResult(id="lock.integrity", status=CheckStatus.PASS, details={}),
            CheckResult(id="override.missing_reference", status=CheckStatus.FAIL, details={}),
        ]
    )
    assert report.ready is False
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
pytest tests/unit/models -v
```

Expected: import/module failures because the models do not exist yet.

- [ ] **Step 4: Implement the models minimally**

Use Pydantic with `extra="forbid"` for project config so software-runtime keys cannot creep into `.ai/project.yaml`. Define `SkillRecord.normalized_id` as `f"{provider_id}.{skill_id}"`; define status enums `pass`, `warning`, `fail`, `partial`; derive `StatusReport.ready` from absence of failing checks.

Core signatures:

```python
class SkillRecord(BaseModel):
    provider_id: str
    skill_id: str
    name: str
    description: str | None = None
    source_path: Path
    source_revision: str | None = None
    content_hash: str
    capabilities: set[str] = Field(default_factory=set)
    harnesses: set[str] = Field(default_factory=set)

    @property
    def normalized_id(self) -> str: ...


class ProjectConfig(BaseModel):
    schema_version: Literal[1]
    project: ProjectInfo
    ai: AIConfig

    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 5: Run model tests, lint, and typing**

Run:

```bash
pytest tests/unit/models -v
ruff check src/skillcord/models tests/unit/models
mypy src/skillcord/models
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/skillcord tests/unit/models
git commit -m "feat: define Skillcord V1 domain models"
```

---

### Task 2: Implement content hashing, normalized IDs, and generic SKILL.md parsing

**Files:**
- Create: `src/skillcord/locking/hashing.py`
- Create: `src/skillcord/normalization/ids.py`
- Create: `src/skillcord/providers/base.py`
- Create: `src/skillcord/providers/generic.py`
- Create: `tests/unit/locking/test_hashing.py`
- Create: `tests/unit/normalization/test_ids.py`
- Create: `tests/unit/providers/test_generic.py`
- Create: `tests/fixtures/providers/generic/SKILL.md`

**Interfaces:**
- Produces: `sha256_file(path: Path) -> str`, `normalize_token(value: str) -> str`, `ProviderAdapter.discover(root: Path) -> ProviderSnapshot`, `GenericSkillAdapter`.
- Consumes: model types from Task 1.

- [ ] **Step 1: Write failing normalization/hash tests**

```python
# tests/unit/normalization/test_ids.py
from skillcord.normalization.ids import normalize_token


def test_normalize_token_is_deterministic() -> None:
    assert normalize_token("Test Driven Development") == "test-driven-development"
    assert normalize_token("test_driven-development") == "test-driven-development"
```

```python
# tests/unit/locking/test_hashing.py
from pathlib import Path
from skillcord.locking.hashing import sha256_file


def test_sha256_file_is_content_based(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_bytes(b"same-content\n")
    first = sha256_file(p)
    p.touch()
    second = sha256_file(p)
    assert first == second
    assert len(first) == 64
```

- [ ] **Step 2: Add a minimal generic SKILL fixture and failing parser test**

Fixture:

```markdown
---
name: Test Driven Development
description: Write a failing test before implementation.
capabilities:
  - tdd
harnesses:
  - claude
  - codex
---

# Test Driven Development
```

Test:

```python
from pathlib import Path
from skillcord.providers.generic import GenericSkillAdapter


def test_generic_adapter_parses_single_skill_file() -> None:
    root = Path("tests/fixtures/providers/generic")
    snapshot = GenericSkillAdapter(provider_id="generic").discover(root)
    assert [s.normalized_id for s in snapshot.skills] == ["generic.test-driven-development"]
    assert snapshot.skills[0].capabilities == {"tdd"}
```

- [ ] **Step 3: Run tests and verify failure**

```bash
pytest tests/unit/normalization tests/unit/locking tests/unit/providers/test_generic.py -v
```

Expected: missing implementation modules/classes.

- [ ] **Step 4: Implement deterministic helpers and strict generic parser**

Rules:
- parse YAML frontmatter only when bounded by `---`;
- reject malformed frontmatter with a structured `ProviderDiscoveryError`;
- do not recursively guess unknown repository structures;
- generic fallback accepts the root `SKILL.md` or explicitly supplied `SKILL.md` path only;
- hash raw bytes, not normalized text.

Base protocol:

```python
class ProviderAdapter(Protocol):
    provider_id: str

    def supports(self, root: Path) -> bool: ...
    def discover(self, root: Path) -> ProviderSnapshot: ...
```

- [ ] **Step 5: Verify tests and static checks**

```bash
pytest tests/unit/normalization tests/unit/locking tests/unit/providers/test_generic.py -v
ruff check src/skillcord/locking src/skillcord/normalization src/skillcord/providers tests/unit
mypy src/skillcord/locking src/skillcord/normalization src/skillcord/providers
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/skillcord/locking src/skillcord/normalization src/skillcord/providers tests
git commit -m "feat: add deterministic skill normalization"
```

---

### Task 3: Implement the Superpowers provider adapter with resolved-component metadata

**Files:**
- Create: `src/skillcord/providers/superpowers.py`
- Create: `tests/unit/providers/test_superpowers.py`
- Create fixture tree: `tests/fixtures/providers/superpowers/skills/brainstorming/SKILL.md`
- Create fixture tree: `tests/fixtures/providers/superpowers/skills/test-driven-development/SKILL.md`
- Create: `tests/fixtures/providers/superpowers/.git/HEAD` only if Git-fixture simulation is needed; prefer injected revision resolver instead of fake Git internals.

**Interfaces:**
- Produces: `SuperpowersAdapter.discover(root: Path) -> ProviderSnapshot`.
- Uses revision resolver interface `RevisionResolver.resolve(path: Path) -> str | None` so unit tests do not need a real Git repo.

- [ ] **Step 1: Write failing provider-specific tests**

```python
from pathlib import Path
from skillcord.providers.superpowers import SuperpowersAdapter


def test_superpowers_discovers_only_skill_tree(fake_revision_resolver) -> None:
    adapter = SuperpowersAdapter(revision_resolver=fake_revision_resolver("abc123"))
    snapshot = adapter.discover(Path("tests/fixtures/providers/superpowers"))
    assert {s.normalized_id for s in snapshot.skills} == {
        "superpowers.brainstorming",
        "superpowers.test-driven-development",
    }
    assert snapshot.components[0].revision == "abc123"
    assert snapshot.components[0].updater_can_mutate is True
```

Add a second test proving unrelated Markdown files are ignored.

- [ ] **Step 2: Run the focused test**

```bash
pytest tests/unit/providers/test_superpowers.py -v
```

Expected: missing adapter.

- [ ] **Step 3: Implement the adapter**

Implementation rules:
- scan only `skills/*/SKILL.md` for current layout;
- normalize metadata through the generic skill parser;
- provider ID is always `superpowers`;
- expose a component record that includes resolved root, optional immutable Git revision, artifact hashes, and `updater_can_mutate=True`;
- do not execute plugin installation/update commands;
- keep component abstraction generic enough for historical/harness-managed layouts in later tests.

- [ ] **Step 4: Verify adapter behavior**

```bash
pytest tests/unit/providers/test_superpowers.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/skillcord/providers/superpowers.py tests/unit/providers/test_superpowers.py tests/fixtures/providers/superpowers
git commit -m "feat: discover Superpowers skills"
```

---

### Task 4: Implement the ECC adapter with partial-support reporting and no hook execution

**Files:**
- Create: `src/skillcord/providers/ecc.py`
- Create: `tests/unit/providers/test_ecc.py`
- Create fixture tree: `tests/fixtures/providers/ecc/skills/...`
- Create fixture tree: `tests/fixtures/providers/ecc/agents/...`
- Create fixture: `tests/fixtures/providers/ecc/hooks/hooks.json`

**Interfaces:**
- Produces: `ECCAdapter.discover(root: Path) -> ProviderSnapshot` with declarative skills plus `unsupported_assets`/`partial_support` metadata.
- Must not import or invoke shell/subprocess execution for hooks.

- [ ] **Step 1: Write failing tests for scale boundaries and hooks**

```python
from pathlib import Path
from skillcord.providers.ecc import ECCAdapter


def test_ecc_discovers_declarative_skills_and_reports_hooks() -> None:
    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))
    assert "ecc.security-review" in {s.normalized_id for s in snapshot.skills}
    assert snapshot.partial_support is True
    assert any(a.kind == "executable-hook" for a in snapshot.unsupported_assets)
```

Add a test monkeypatching `subprocess.run` to raise if called; discovery must still pass.

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/unit/providers/test_ecc.py -v
```

Expected: missing adapter.

- [ ] **Step 3: Implement ECC layout handling**

Rules:
- discover declarative `skills/**/SKILL.md` according to fixture-supported known paths;
- record agents/commands as supporting metadata only when schema can be parsed safely;
- record executable hooks as unsupported/inactive, never run them;
- provider snapshot status must surface partial support;
- do not compare every ECC skill pairwise with every other provider: only normalize records here.

- [ ] **Step 4: Verify tests**

```bash
pytest tests/unit/providers/test_ecc.py -v
```

Expected: pass and zero subprocess calls.

- [ ] **Step 5: Commit**

```bash
git add src/skillcord/providers/ecc.py tests/unit/providers/test_ecc.py tests/fixtures/providers/ecc
git commit -m "feat: discover ECC declarative assets safely"
```

---

### Task 5: Implement Open Design and Humanizer provider adapters

**Files:**
- Create: `src/skillcord/providers/open_design.py`
- Create: `src/skillcord/providers/humanizer.py`
- Create: `tests/unit/providers/test_open_design.py`
- Create: `tests/unit/providers/test_humanizer.py`
- Create fixtures: `tests/fixtures/providers/open_design/...`
- Create fixture: `tests/fixtures/providers/humanizer/SKILL.md`

**Interfaces:**
- Produces: `OpenDesignAdapter`, `HumanizerAdapter`.
- Humanizer snapshot must include an explicit policy metadata flag `execution_mode="harness_only"`.

- [ ] **Step 1: Write failing Open Design test**

```python
from pathlib import Path
from skillcord.providers.open_design import OpenDesignAdapter


def test_open_design_discovers_declarative_skills_only() -> None:
    snapshot = OpenDesignAdapter().discover(Path("tests/fixtures/providers/open_design"))
    assert snapshot.skills
    assert snapshot.runtime_requirements == {"mcp": "external", "daemon": "external"}
```

- [ ] **Step 2: Write failing Humanizer test**

```python
from pathlib import Path
from skillcord.providers.humanizer import HumanizerAdapter


def test_humanizer_is_harness_only() -> None:
    snapshot = HumanizerAdapter().discover(Path("tests/fixtures/providers/humanizer"))
    assert snapshot.skills[0].normalized_id == "humanizer.humanizer"
    assert snapshot.skills[0].execution_mode == "harness_only"
```

- [ ] **Step 3: Run and confirm failure**

```bash
pytest tests/unit/providers/test_open_design.py tests/unit/providers/test_humanizer.py -v
```

- [ ] **Step 4: Implement both adapters**

Open Design rules:
- discover only known declarative skill paths;
- report MCP/daemon as external runtime not managed by V1;
- do not launch or install anything.

Humanizer rules:
- parse the root skill artifact;
- set explicit optional/harness-only metadata;
- never expose a callable that sends text anywhere.

- [ ] **Step 5: Verify no accidental text-egress surface**

Run:

```bash
pytest tests/unit/providers/test_open_design.py tests/unit/providers/test_humanizer.py -v
rg -n "requests|httpx|urllib|openai|anthropic|humanize\(" src/skillcord/providers src/skillcord || true
```

Expected: tests pass; no model/network client implementation in Skillcord V1.

- [ ] **Step 6: Commit**

```bash
git add src/skillcord/providers/open_design.py src/skillcord/providers/humanizer.py tests/unit/providers tests/fixtures/providers/open_design tests/fixtures/providers/humanizer
git commit -m "feat: add Open Design and Humanizer adapters"
```

---

### Task 6: Build provider/harness discovery orchestration

**Files:**
- Create: `src/skillcord/discovery/sources.py`
- Create: `src/skillcord/discovery/harnesses.py`
- Create: `src/skillcord/discovery/service.py`
- Create: `tests/unit/discovery/test_sources.py`
- Create: `tests/unit/discovery/test_harnesses.py`
- Create: `tests/integration/test_provider_discovery.py`

**Interfaces:**
- Produces: `DiscoveryService.scan(project_root: Path, source_roots: Mapping[str, Path]) -> DiscoveryResult`.
- `DiscoveryResult` contains provider snapshots, harness detections, warnings, and unsupported layouts.

- [ ] **Step 1: Write failing orchestration test**

```python
from pathlib import Path
from skillcord.discovery.service import DiscoveryService


def test_scan_uses_explicit_adapter_per_known_provider(provider_fixture_roots) -> None:
    result = DiscoveryService.default().scan(
        project_root=Path("."), source_roots=provider_fixture_roots
    )
    assert {p.provider_id for p in result.providers} == {
        "superpowers", "ecc", "open_design", "humanizer"
    }
```

- [ ] **Step 2: Write harness detection tests**

Use temporary directories/config hints, not global machine state. `HarnessDetector` should accept injected candidate paths so tests are hermetic.

- [ ] **Step 3: Run and confirm failure**

```bash
pytest tests/unit/discovery tests/integration/test_provider_discovery.py -v
```

- [ ] **Step 4: Implement service and explicit adapter registry**

Implement:

```python
@dataclass(frozen=True)
class AdapterRegistration:
    provider_id: str
    adapter: ProviderAdapter


class DiscoveryService:
    def scan(
        self,
        project_root: Path,
        source_roots: Mapping[str, Path],
    ) -> DiscoveryResult: ...
```

Known provider IDs must map to explicit adapters. Unknown providers only use the generic parser when the caller explicitly points at a well-formed SKILL.md-compatible root.

- [ ] **Step 5: Verify**

```bash
pytest tests/unit/discovery tests/integration/test_provider_discovery.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/skillcord/discovery tests/unit/discovery tests/integration/test_provider_discovery.py
git commit -m "feat: orchestrate provider and harness discovery"
```

---

### Task 7: Implement deterministic capability aliases and grouped overlap engine

**Files:**
- Create: `registry/aliases/capabilities.yaml`
- Create: `src/skillcord/overlap/aliases.py`
- Create: `src/skillcord/overlap/grouping.py`
- Create: `src/skillcord/overlap/resolution.py`
- Create: `tests/unit/overlap/test_aliases.py`
- Create: `tests/unit/overlap/test_grouping.py`
- Create: `tests/unit/overlap/test_resolution.py`

**Interfaces:**
- Produces: `AliasRegistry`, `group_capabilities(skills, aliases, overrides) -> list[CapabilityGroup]`, `ConflictResolver`.
- No pairwise semantic similarity API exists in V1.

- [ ] **Step 1: Define a small explicit alias fixture/registry**

```yaml
schema_version: 1
aliases:
  test-driven-development:
    - tdd
    - test-first-development
  code-review:
    - code-review
    - review-code
```

This is alias normalization only, not provider ownership.

- [ ] **Step 2: Write failing tests for deterministic grouping**

```python
def test_tdd_aliases_group_across_providers(skill_factory, alias_registry) -> None:
    skills = [
        skill_factory("superpowers", "test-driven-development", {"test-driven-development"}),
        skill_factory("ecc", "tdd", {"tdd"}),
    ]
    groups = group_capabilities(skills, alias_registry, overrides={})
    assert len(groups) == 1
    assert groups[0].capability_id == "test-driven-development"
    assert groups[0].default_action == "keep-all"
```

Add a test proving similar descriptions with unrelated names/tags **do not** group.

- [ ] **Step 3: Write question-budget test**

```python
def test_resolver_prompts_at_most_ten_groups(...):
    resolver = ConflictResolver(question_budget=10)
    result = resolver.resolve(groups=make_12_groups(), decisions={})
    assert result.prompted_count == 10
    assert result.deferred_count == 2
    assert all(g.action == "keep-all" for g in result.deferred_groups)
```

- [ ] **Step 4: Run tests and verify failure**

```bash
pytest tests/unit/overlap -v
```

- [ ] **Step 5: Implement the overlap engine**

Grouping evidence is only:
- normalized capability/skill name;
- registry alias;
- provider-declared capability/tag;
- existing override linking referenced skills.

Do not add `description_similarity`, fuzzy string scoring, embeddings, or LLM hooks.

- [ ] **Step 6: Verify deterministic output ordering**

```bash
pytest tests/unit/overlap -v
```

Ensure groups and candidates sort by stable IDs so golden tests do not flap.

- [ ] **Step 7: Commit**

```bash
git add registry/aliases src/skillcord/overlap tests/unit/overlap
git commit -m "feat: group deterministic capability overlaps"
```

---

### Task 8: Implement override persistence, validation, and rename-drift detection

**Files:**
- Create: `src/skillcord/decisions/store.py`
- Create: `src/skillcord/decisions/validation.py`
- Create: `tests/unit/decisions/test_store.py`
- Create: `tests/unit/decisions/test_validation.py`

**Interfaces:**
- Produces: `DecisionStore.load/save`, `validate_override_references(overrides, discovered_ids) -> list[CheckResult]`.
- Consumes: normalized skill IDs and `OverrideConfig`.

- [ ] **Step 1: Write failing round-trip test**

```python
from skillcord.decisions.store import DecisionStore


def test_override_round_trip(tmp_path) -> None:
    path = tmp_path / ".ai" / "overrides.yaml"
    store = DecisionStore(path)
    store.save_decision(
        capability_id="test-driven-development",
        prefer="superpowers.test-driven-development",
        suppress=["ecc.tdd"],
    )
    loaded = store.load()
    assert loaded.overrides["test-driven-development"].prefer == \
        "superpowers.test-driven-development"
```

- [ ] **Step 2: Write failing missing-reference test**

```python
def test_missing_override_reference_is_failure() -> None:
    checks = validate_override_references(
        overrides=override_fixture(prefer="ecc.old-tdd"),
        discovered_ids={"superpowers.test-driven-development", "ecc.tdd"},
    )
    assert checks[0].id == "override.missing_reference"
    assert checks[0].status.value == "fail"
```

- [ ] **Step 3: Run and confirm failure**

```bash
pytest tests/unit/decisions -v
```

- [ ] **Step 4: Implement atomic YAML writes and validation**

Use write-to-temp + `Path.replace()` for project decision files. Preserve stable key ordering. Never silently delete invalid decisions.

- [ ] **Step 5: Verify**

```bash
pytest tests/unit/decisions -v
```

- [ ] **Step 6: Commit**

```bash
git add src/skillcord/decisions tests/unit/decisions
git commit -m "feat: persist and validate capability decisions"
```

---

### Task 9: Implement immutable revision resolution, lock generation, and drift checks

**Files:**
- Create: `src/skillcord/locking/revisions.py`
- Create: `src/skillcord/locking/service.py`
- Create: `tests/unit/locking/test_revisions.py`
- Create: `tests/unit/locking/test_service.py`
- Create: `tests/integration/test_lock_drift.py`

**Interfaces:**
- Produces: `RevisionResolver`, `LockService.build(snapshot_set, resolved_override_ids) -> LockFile`, `LockService.check_drift(lock) -> list[CheckResult]`.

- [ ] **Step 1: Write failing revision-resolver tests**

Test a real temporary Git repository created inside the test, not the user's repo:

```python
def test_git_revision_resolver_returns_commit_sha(tmp_git_repo) -> None:
    revision = GitRevisionResolver().resolve(tmp_git_repo)
    assert len(revision) == 40
```

Add a non-Git test returning `None` rather than guessing a version.

- [ ] **Step 2: Write failing lock-generation test**

Assert lock components record:
- adapter/provider ID;
- resolved local source identity/path;
- optional immutable revision;
- `updater_can_mutate`;
- artifact resolved path + SHA-256;
- `resolved_ids` referenced by overrides.

- [ ] **Step 3: Write failing updater-drift integration test**

Build lock from fixture, mutate `SKILL.md`, run drift check, expect `lock.artifact_hash` fail even though source path/provider ID are unchanged.

- [ ] **Step 4: Run and confirm failure**

```bash
pytest tests/unit/locking tests/integration/test_lock_drift.py -v
```

- [ ] **Step 5: Implement lock service**

Important rule: do not assume one Git repo equals one provider. `ProviderSnapshot.components` is authoritative; hash exactly the resolved artifacts the adapter returned.

- [ ] **Step 6: Verify lock serialization is stable**

```bash
pytest tests/unit/locking tests/integration/test_lock_drift.py -v
```

Run the same build twice and assert byte-identical YAML output.

- [ ] **Step 7: Commit**

```bash
git add src/skillcord/locking tests/unit/locking tests/integration/test_lock_drift.py
git commit -m "feat: pin resolved provider content and detect drift"
```

---

### Task 10: Implement managed-block parsing and byte-preserving writes

**Files:**
- Create: `src/skillcord/managed_blocks/parser.py`
- Create: `src/skillcord/managed_blocks/writer.py`
- Create: `tests/unit/managed_blocks/test_parser.py`
- Create: `tests/unit/managed_blocks/test_writer.py`

**Interfaces:**
- Produces: `parse_managed_block(text: str) -> ManagedBlockState`, `render_managed_update(original: bytes, replacement: bytes) -> bytes`.

- [ ] **Step 1: Write failing preservation test**

```python
from skillcord.managed_blocks.writer import update_managed_block


def test_user_content_outside_block_is_byte_preserved() -> None:
    original = (
        b"# My Project\r\n"
        b"User text  \r\n"
        b"<!-- skillcord:begin -->\r\nold\r\n<!-- skillcord:end -->\r\n"
        b"tail\r\n"
    )
    updated = update_managed_block(original, b"new\n")
    assert updated.startswith(b"# My Project\r\nUser text  \r\n")
    assert updated.endswith(b"tail\r\n")
```

- [ ] **Step 2: Write malformed-marker tests**

Cover:
- begin without end;
- end without begin;
- duplicate blocks;
- nested begin marker.

Each must raise `ManagedBlockError`; no best-effort repair.

- [ ] **Step 3: Run and confirm failure**

```bash
pytest tests/unit/managed_blocks -v
```

- [ ] **Step 4: Implement byte-oriented managed-block update**

Operate on raw bytes to preserve user-authored newline style/spacing outside the region. Appending a missing block may use the file's detected newline convention; empty file defaults to `\n`.

- [ ] **Step 5: Verify**

```bash
pytest tests/unit/managed_blocks -v
```

- [ ] **Step 6: Commit**

```bash
git add src/skillcord/managed_blocks tests/unit/managed_blocks
git commit -m "feat: preserve user content with managed blocks"
```

---

### Task 11: Generate universal AGENTS policy plus Claude/Codex adapters

**Files:**
- Create: `src/skillcord/adapters/base.py`
- Create: `src/skillcord/adapters/agents_md.py`
- Create: `src/skillcord/adapters/claude.py`
- Create: `src/skillcord/adapters/codex.py`
- Create: `templates/agents_managed.md.j2`
- Create: `templates/claude_managed.md.j2`
- Create: `templates/humanizer_policy.md.j2`
- Create: `tests/unit/adapters/test_agents_md.py`
- Create: `tests/unit/adapters/test_claude.py`
- Create: `tests/unit/adapters/test_codex.py`

**Interfaces:**
- Produces: `HarnessAdapter.plan(project, active_skills, decisions) -> list[GeneratedArtifact]`.
- `GeneratedArtifact` includes path, ownership (`managed_block` or `owned_file`), bytes content, and source capability IDs.

- [ ] **Step 1: Write failing AGENTS policy test**

Assert generated content includes only Skillcord-managed policy and active capability references, and does **not** copy arbitrary full provider prompt bodies into AGENTS.md.

- [ ] **Step 2: Write Humanizer policy test**

```python
def test_humanizer_policy_is_opt_in() -> None:
    text = render_agents_policy(active_ids={"humanizer.humanizer"})
    assert "explicitly requests prose humanization" in text
    assert "Do not automatically apply" in text
```

- [ ] **Step 3: Write Claude/Codex isolation tests**

Claude-specific content must not leak into Codex adapter output and vice versa. Both use the same universal managed policy source.

- [ ] **Step 4: Run and confirm failure**

```bash
pytest tests/unit/adapters -v
```

- [ ] **Step 5: Implement adapters and templates**

Design:

```python
class HarnessAdapter(Protocol):
    harness_id: str
    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]: ...
```

Keep agent-specific files minimal. If the harness can consume `AGENTS.md` directly, prefer reference/config over duplicated policy text.

- [ ] **Step 6: Verify outputs are deterministic**

```bash
pytest tests/unit/adapters -v
```

- [ ] **Step 7: Commit**

```bash
git add src/skillcord/adapters templates tests/unit/adapters
git commit -m "feat: generate universal and harness-specific AI policy"
```

---

### Task 12: Build sync planning, diff preview, selective removal semantics, and safe application

**Files:**
- Create: `src/skillcord/sync/planner.py`
- Create: `src/skillcord/sync/applier.py`
- Create: `tests/unit/sync/test_planner.py`
- Create: `tests/unit/sync/test_applier.py`
- Create: `tests/integration/test_sync_managed_files.py`

**Interfaces:**
- Produces: `SyncPlanner.plan(...) -> SyncPlan`, `SyncPlan.diff_text() -> str`, `SyncApplier.apply(plan, approved: bool) -> ApplyResult`.

- [ ] **Step 1: Write failing no-mutation-before-approval test**

```python
def test_sync_plan_does_not_write_files(tmp_path, sync_context) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# user\n", encoding="utf-8")
    plan = SyncPlanner().plan(sync_context.for_root(tmp_path))
    assert agents.read_text(encoding="utf-8") == "# user\n"
    assert "skillcord:begin" in plan.diff_text()
```

- [ ] **Step 2: Write removal-semantics test**

Disable Humanizer after it was previously managed. New plan must remove only its managed reference/policy and preserve user text/provider installation.

- [ ] **Step 3: Write non-interactive fail-closed tests**

Cover:
- malformed block => fail;
- missing override ID => fail;
- dedicated generated file exists with unknown user content and cannot be safely merged => fail;
- ordinary unresolved overlap group => allowed with keep-all;
- unresolved `single_owner_required=True` group => fail.

- [ ] **Step 4: Run and confirm failure**

```bash
pytest tests/unit/sync tests/integration/test_sync_managed_files.py -v
```

- [ ] **Step 5: Implement planner/applier**

Planner computes exact byte changes and unified diffs. Applier accepts only a precomputed plan and uses atomic writes. Never re-resolve providers during apply.

- [ ] **Step 6: Verify**

```bash
pytest tests/unit/sync tests/integration/test_sync_managed_files.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/skillcord/sync tests/unit/sync tests/integration/test_sync_managed_files.py
git commit -m "feat: plan and apply safe AI configuration sync"
```

---

### Task 13: Implement doctor checks and machine-readable status

**Files:**
- Create: `src/skillcord/status/checks.py`
- Create: `src/skillcord/status/doctor.py`
- Create: `src/skillcord/status/serializer.py`
- Create: `tests/unit/status/test_doctor.py`
- Create: `tests/unit/status/test_serializer.py`
- Create: `tests/integration/test_doctor_provider_drift.py`

**Interfaces:**
- Produces: `Doctor.run(context) -> StatusReport`, `serialize_status(report) -> str`.
- Stable check IDs include at minimum: `schema.project`, `provider.presence`, `lock.integrity`, `override.missing_reference`, `adapter.drift`, `conflicts.unresolved`, `provider.partial_support`, `humanizer.policy`.

- [ ] **Step 1: Write failing status-schema test**

```python
import json
from skillcord.status.serializer import serialize_status


def test_status_json_has_stable_machine_fields(status_report) -> None:
    payload = json.loads(serialize_status(status_report))
    assert payload["schema_version"] == 1
    assert payload["timestamp"].endswith("Z")
    assert isinstance(payload["checks"], list)
    assert all("id" in check and "status" in check for check in payload["checks"])
```

- [ ] **Step 2: Write failing partial ECC support test**

When ECC hooks exist but requested capabilities use only supported declarative assets, doctor reports warning/partial rather than hard fail. If project explicitly requires an unsupported executable hook capability, doctor fails.

- [ ] **Step 3: Write drift and missing-reference integration tests**

Use lock fixture from Task 9 and override fixture from Task 8.

- [ ] **Step 4: Run and confirm failure**

```bash
pytest tests/unit/status tests/integration/test_doctor_provider_drift.py -v
```

- [ ] **Step 5: Implement doctor aggregation**

`ready` is derived: any `fail` => false; warnings/partial alone do not necessarily make false unless a requested capability is unsatisfied.

- [ ] **Step 6: Verify deterministic JSON**

Freeze timestamp through injected clock in tests; sort checks by stable ID.

```bash
pytest tests/unit/status tests/integration/test_doctor_provider_drift.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/skillcord/status tests/unit/status tests/integration/test_doctor_provider_drift.py
git commit -m "feat: add AI runtime doctor and status schema"
```

---

### Task 14: Add history/audit logging for Skillcord-managed changes

**Files:**
- Create: `src/skillcord/history/store.py`
- Create: `tests/unit/history/test_store.py`
- Modify: `src/skillcord/sync/applier.py`

**Interfaces:**
- Produces: `HistoryStore.append(entry)`, `HistoryStore.list() -> list[HistoryEntry]`.
- Sync applier records only Skillcord-managed file changes; no universal rollback API.

- [ ] **Step 1: Write failing history test**

```python
def test_history_records_files_and_provider_revision(tmp_path) -> None:
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")
    store.append(
        HistoryEntry(
            timestamp="2026-08-11T17:00:00Z",
            action="sync",
            files_changed=["AGENTS.md"],
            provider_revisions={"superpowers": {"old": "a", "new": "b"}},
            deterministic_inverse=False,
        )
    )
    assert store.list()[0].action == "sync"
```

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/unit/history -v
```

- [ ] **Step 3: Implement JSONL audit storage and sync hook**

Do not call the feature rollback. If an exact inverse is available for a managed-block edit, record that fact; never promise package/provider uninstall reversal.

- [ ] **Step 4: Verify**

```bash
pytest tests/unit/history tests/unit/sync -v
```

- [ ] **Step 5: Commit**

```bash
git add src/skillcord/history src/skillcord/sync/applier.py tests/unit/history
git commit -m "feat: record Skillcord configuration history"
```

---

### Task 15: Implement CLI commands and interactive/non-interactive flows

**Files:**
- Create: `src/skillcord/cli/app.py`
- Create: `src/skillcord/cli/rendering.py`
- Create: `tests/integration/cli/test_init.py`
- Create: `tests/integration/cli/test_scan.py`
- Create: `tests/integration/cli/test_conflicts.py`
- Create: `tests/integration/cli/test_sync.py`
- Create: `tests/integration/cli/test_doctor.py`
- Create: `tests/integration/cli/test_status.py`
- Create: `tests/integration/cli/test_history.py`

**Interfaces:**
- CLI commands: `init`, `scan`, `conflicts`, `sync`, `doctor`, `diff`, `status --json`, `history`.
- Internal command functions should accept injectable services to keep CLI tests hermetic.

- [ ] **Step 1: Write failing `init` test**

Use Typer `CliRunner`:

```python
def test_init_creates_ai_only_project_config(tmp_path, monkeypatch) -> None:
    result = runner.invoke(app, ["init", "--project-root", str(tmp_path), "--harness", "claude"])
    assert result.exit_code == 0
    text = (tmp_path / ".ai" / "project.yaml").read_text()
    assert "harnesses:" in text
    assert "node:" not in text
```

- [ ] **Step 2: Write scan/conflicts tests**

Assert scan output is stable and conflicts are grouped by capability, not pairwise rows. Verify default budget 10 and keep-all summary beyond budget.

- [ ] **Step 3: Write interactive sync approval test**

User answers `n`: file unchanged. User answers `y`: only managed block changes.

- [ ] **Step 4: Write non-interactive sync tests**

Test deterministic success with unresolved keep-all groups, and failure for malformed markers/missing refs/single-owner unresolved policy.

- [ ] **Step 5: Write doctor/status/history CLI tests**

`status --json` stdout must contain JSON only; diagnostics go to stderr if needed.

- [ ] **Step 6: Run and confirm failures**

```bash
pytest tests/integration/cli -v
```

- [ ] **Step 7: Implement CLI orchestration**

Typer commands call existing domain services; no business logic in command functions. Use exit codes:
- `0`: success/ready;
- `1`: expected not-ready/conflict/policy failure;
- `2`: invalid usage/schema;
- `3`: unsafe/non-deterministic mutation refused.

- [ ] **Step 8: Verify**

```bash
pytest tests/integration/cli -v
```

- [ ] **Step 9: Commit**

```bash
git add src/skillcord/cli tests/integration/cli
git commit -m "feat: expose Skillcord V1 CLI workflows"
```

---

### Task 16: Add golden-file end-to-end fixtures for the complete V1 flow

**Files:**
- Create: `tests/golden/test_end_to_end.py`
- Create: `tests/golden/expected/AGENTS.md`
- Create: `tests/golden/expected/CLAUDE.md`
- Create: `tests/golden/expected/ai-lock.yaml`
- Create: `tests/golden/expected/status.json`
- Create: `tests/fixtures/projects/multi-provider/.ai/project.yaml`
- Create: `tests/fixtures/projects/multi-provider/.ai/overrides.yaml`
- Create: `tests/fixtures/projects/multi-provider/AGENTS.md`

**Interfaces:**
- Exercises Discovery → Normalization → Overlap → Decisions → Lock → Adapter planning → Managed-block sync → Doctor/status.

- [ ] **Step 1: Create a deterministic fixture project**

Include all four provider fixtures, a known TDD overlap, Humanizer enabled as optional, user-authored AGENTS prefix/suffix, and one ECC hook fixture that must remain inactive.

- [ ] **Step 2: Write failing golden test**

```python
def test_multi_provider_project_matches_reviewed_golden(golden_runner) -> None:
    output = golden_runner.run("tests/fixtures/projects/multi-provider")
    assert output.agents_md == read_bytes("tests/golden/expected/AGENTS.md")
    assert output.claude_md == read_bytes("tests/golden/expected/CLAUDE.md")
    assert output.lock_yaml == read_bytes("tests/golden/expected/ai-lock.yaml")
    assert output.status_json == read_bytes("tests/golden/expected/status.json")
```

- [ ] **Step 3: Run and inspect the failure**

```bash
pytest tests/golden/test_end_to_end.py -vv
```

Expected: mismatch/missing golden files.

- [ ] **Step 4: Generate candidate outputs once, review manually, then commit as golden fixtures**

Do **not** create an auto-accept mode that updates golden files during ordinary test runs. Golden updates must be explicit review events.

- [ ] **Step 5: Verify golden stability across two runs**

```bash
pytest tests/golden/test_end_to_end.py -v
pytest tests/golden/test_end_to_end.py -v
```

Expected: byte-identical pass both times.

- [ ] **Step 6: Commit**

```bash
git add tests/golden tests/fixtures/projects/multi-provider
git commit -m "test: lock down Skillcord V1 generated artifacts"
```

---

### Task 17: Add network/process safety tests and complete V1 verification gates

**Files:**
- Create: `tests/integration/test_no_network_or_hook_execution.py`
- Create: `tests/integration/test_no_host_package_mutation.py`
- Modify: `pyproject.toml`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces the CI acceptance gate for V1.

- [ ] **Step 1: Write a test that forbids network calls**

Monkeypatch `socket.socket.connect`, `urllib.request.urlopen`, and any selected HTTP library entry points to raise. Run provider discovery, overlap grouping, sync planning, and doctor. All must pass without network.

- [ ] **Step 2: Write a test that forbids arbitrary process execution during provider discovery**

Monkeypatch `subprocess.run`, `subprocess.Popen`, `os.system` to raise for discovery/sync. Revision-resolver tests may use an injected safe Git resolver separately; default runtime discovery must not execute provider hooks.

- [ ] **Step 3: Add static forbidden-command regression check**

Test source text for accidental orchestration of:

```text
winget install
apt install
brew install
dnf install
pacman -S
curl | bash
```

A documentation fixture is allowed only outside executable source paths.

- [ ] **Step 4: Run safety tests**

```bash
pytest tests/integration/test_no_network_or_hook_execution.py tests/integration/test_no_host_package_mutation.py -v
```

- [ ] **Step 5: Add CI**

CI matrix may use Linux/macOS/Windows Python runners, but tests must not install project runtimes/providers or mutate host package managers. Steps:

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with:
    python-version: "3.11"
- run: python -m pip install -e '.[dev]'
- run: ruff check .
- run: mypy src/skillcord
- run: pytest --cov=skillcord --cov-report=term-missing
```

- [ ] **Step 6: Run the full local verification suite**

```bash
ruff check .
mypy src/skillcord
pytest --cov=skillcord --cov-report=term-missing
```

Expected: all pass. Coverage target for V1 core (`providers`, `overlap`, `decisions`, `locking`, `managed_blocks`, `sync`, `status`) >= 90% line coverage.

- [ ] **Step 7: Commit**

```bash
git add tests/integration pyproject.toml .github/workflows/ci.yml
git commit -m "test: enforce Skillcord V1 safety boundaries"
```

---

### Task 18: Write user-facing V1 documentation and enforce the pre-release naming gate

**Files:**
- Create/Modify: `README.md`
- Create: `docs/providers.md`
- Create: `docs/conflicts.md`
- Create: `docs/security.md`
- Create: `docs/schemas/project.schema.example.yaml`
- Create: `docs/schemas/overrides.schema.example.yaml`
- Create: `docs/schemas/lock.schema.example.yaml`
- Create: `docs/release-checklist.md`
- Create: `tests/unit/docs/test_release_gate.py`

**Interfaces:**
- Documents the exact V1 boundary and prevents accidental publication under the internal codename.

- [ ] **Step 1: Write failing release-gate test**

```python
from pathlib import Path


def test_public_release_requires_name_gate() -> None:
    checklist = Path("docs/release-checklist.md").read_text(encoding="utf-8")
    assert "PyPI namespace re-check" in checklist
    assert "semantic name collision review" in checklist
    assert "Skillcord is an internal codename" in checklist
```

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/unit/docs/test_release_gate.py -v
```

- [ ] **Step 3: Write concise V1 docs**

README must clearly state:
- what V1 does;
- what V1 explicitly does not do;
- four provider support boundaries;
- deterministic overlap logic;
- keep-all default;
- Humanizer opt-in/harness-only policy;
- managed-block ownership;
- no OS/package-manager mutation;
- no network/model use for overlap;
- local-development CLI uses internal codename only.

- [ ] **Step 4: Add release checklist**

Before any public package/CLI release, require explicit completion of:
- PyPI namespace re-check;
- GitHub/repository name re-check;
- CLI collision check;
- semantic name collision review;
- basic trademark/web search;
- rename internal package/entrypoint if public name changes;
- full golden/safety test suite after rename.

- [ ] **Step 5: Verify docs and all tests**

```bash
pytest tests/unit/docs/test_release_gate.py -v
ruff check .
mypy src/skillcord
pytest --cov=skillcord --cov-report=term-missing
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs tests/unit/docs
git commit -m "docs: define Skillcord V1 usage and release gate"
```

---

## Acceptance-Criteria Coverage Matrix

| Spec criterion | Covered by | Proof type |
|---|---|---|
| 1. Initialize without OS mutation | Tasks 15, 17 | CLI integration + mutation-safety tests |
| 2. Discover all four initial providers | Tasks 3–6, 16 | Provider unit/integration + golden tests |
| 3. Stable normalized IDs and content hashes | Tasks 1, 2, 9 | Unit + lock tests |
| 4. Surface known overlap without silent suppression | Tasks 7, 16 | Overlap unit + golden tests |
| 5. Persist and reuse user conflict decisions | Tasks 7, 8, 16 | Decision round-trip + E2E tests |
| 6. Unknown/non-deterministic similarity never disables a skill | Task 7 | Test that description similarity alone does not group/suppress |
| 7. Lock immutable revision + applied-content hashes | Task 9 | Lock serialization tests |
| 8. Detect modified locked artifact | Tasks 9, 13 | Drift integration + doctor tests |
| 9. `project.yaml` avoids runtime-version duplication | Tasks 1, 15 | Schema rejection + CLI init tests |
| 10. Shared AGENTS policy with isolated Claude/Codex adapters | Tasks 11, 16 | Adapter unit + golden tests |
| 11. Deterministic/non-interactive fail-closed semantics | Tasks 12, 15 | Sync unit + CLI integration tests |
| 12. Structured `status --json` with derived readiness | Task 13, 15 | Serializer + CLI tests |
| 13. Humanizer discovered/configured but never run by Skillcord | Tasks 5, 11, 17 | Provider/policy + no-network tests |
| 14. Provider update cannot silently invalidate owner override | Tasks 8, 9, 13 | Missing-reference + drift doctor tests |
| 15. Managed AGENTS block preserves outside bytes | Tasks 10, 12, 16 | Byte-preservation + E2E golden tests |
| 16. Grouped conflict UX, keep-all default, question budget | Tasks 7, 15 | Resolver + CLI tests |
| 17. Explicit provider adapters; unsupported runtimes/hooks are partial | Tasks 3–6, 13, 16 | Provider + doctor + E2E tests |
| 18. No host-package mutation and no model/network overlap calls | Task 17 | Safety integration + CI gates |

## Final Verification Checklist

Run only after Tasks 1-18 are complete:

```bash
git status --short
ruff check .
mypy src/skillcord
pytest --cov=skillcord --cov-report=term-missing
pytest tests/golden/test_end_to_end.py -v
```

Expected:
- Git working tree contains only intentionally uncommitted review changes, preferably none.
- Ruff: zero errors.
- mypy: zero errors.
- pytest: all tests pass.
- Core V1 modules have >=90% line coverage.
- Golden outputs are stable on repeated runs.
- No test requires network/model access.
- No test invokes OS package managers.

Then manually exercise against copies of the four provider fixture layouts only:

```bash
skillcord-dev scan
skillcord-dev conflicts
skillcord-dev diff
skillcord-dev sync
skillcord-dev doctor
skillcord-dev status --json
skillcord-dev history
```

Confirm:
1. conflicts are grouped by capability;
2. no unreviewed provider is suppressed;
3. question budget stops at 10 by default;
4. Humanizer appears as optional/harness-only;
5. ECC hooks appear as partial/unsupported rather than executing;
6. Open Design MCP/daemon is not started;
7. AGENTS.md outside the managed block is unchanged byte-for-byte;
8. drifted provider content is reported;
9. missing override IDs fail explicitly;
10. `status --json` is parseable and contains stable check IDs.

## V1 Completion Definition

V1 is complete only when all 18 acceptance criteria from `docs/superpowers/specs/2026-08-11-skillcord-runtime-design.md` are covered by at least one automated test or explicit release-gate check, and the final verification suite above passes.

V1 completion does **not** authorize starting V1.1/V2 work in the same branch. Environment inspection/bootstrap work requires its own plan and review.
