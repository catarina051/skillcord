from dataclasses import replace
from pathlib import Path

import pytest

from skillcord.adapters.base import AdapterContext, GeneratedArtifact
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.adapters.codex import CodexAdapter
from skillcord.models.capability import CapabilityGroup
from skillcord.models.config import (
    AIConfig,
    CapabilityOverride,
    OverrideConfig,
    ProjectConfig,
    ProjectInfo,
)
from skillcord.models.provider import SkillRecord
from skillcord.sync.planner import (
    OwnedFileConflictError,
    SyncContext,
    SyncPlanner,
    UnresolvedConflictError,
    UnsafeTargetPathError,
)


def _skill(root: Path, provider_id: str, skill_id: str) -> SkillRecord:
    return SkillRecord(
        provider_id=provider_id,
        skill_id=skill_id,
        name=skill_id,
        source_path=root / "providers" / provider_id / skill_id / "SKILL.md",
        content_hash="a" * 64,
    )


def _context(
    root: Path,
    *,
    active_skills: tuple[SkillRecord, ...] | None = None,
    decisions: OverrideConfig | None = None,
    adapters: tuple[object, ...] = (ClaudeAdapter(), CodexAdapter()),
    discovered_skill_ids: frozenset[str] | None = None,
    conflict_groups: tuple[CapabilityGroup, ...] = (),
    non_interactive: bool = False,
) -> SyncContext:
    skills = active_skills or (_skill(root, "superpowers", "brainstorming"),)
    adapter_context = AdapterContext(
        project=ProjectConfig(
            schema_version=1,
            project=ProjectInfo(intent="api"),
            ai=AIConfig(harnesses=["claude", "codex"], desired_capabilities=["planning"]),
        ),
        active_skills=skills,
        decisions=decisions or OverrideConfig(schema_version=1),
    )
    return SyncContext(
        project_root=root,
        adapter_context=adapter_context,
        adapters=adapters,
        discovered_skill_ids=discovered_skill_ids,
        conflict_groups=conflict_groups,
        non_interactive=non_interactive,
    )


def test_sync_plan_does_not_write_files(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# user\n", encoding="utf-8")
    before = agents.read_bytes()

    plan = SyncPlanner().plan(_context(tmp_path))

    assert agents.read_text(encoding="utf-8") == "# user\n"
    assert "skillcord:begin" in plan.diff_text()
    assert plan.changes[0].before == before
    assert plan.changes[0].after.startswith(before)


def test_diff_marks_changed_lines_without_final_newlines(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(
        b"<!-- skillcord:begin -->\nold without newline\n<!-- skillcord:end -->"
    )

    diff = SyncPlanner().plan(_context(tmp_path)).diff_text()

    assert "-<!-- skillcord:end -->\n\\ No newline at end of file\n" in diff
    assert "-<!-- skillcord:end -->+" not in diff


def test_diff_distinguishes_absent_file_from_existing_empty_file(tmp_path: Path) -> None:
    absent = SyncPlanner().plan(
        _context(tmp_path, adapters=(_OwnedFileAdapter(),))
    ).diff_text()
    (tmp_path / "generated.json").write_bytes(b"")
    empty = SyncPlanner().plan(
        replace(
            _context(tmp_path, adapters=(_OwnedFileAdapter(),)),
            owned_file_baselines={Path("generated.json"): b""},
        )
    ).diff_text()

    assert "--- /dev/null\n" in absent
    assert "--- a/generated.json\n" in empty


def test_diff_escapes_invalid_utf8_without_ambiguity(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"literal \\xff and byte \xff\n")

    diff = SyncPlanner().plan(_context(tmp_path)).diff_text()

    assert diff.startswith("# Non-UTF-8 diff bytes use unambiguous \\xNN escapes; ")
    assert "literal \\\\xff and byte \\xff" in diff


def test_diff_previews_creation_of_empty_owned_file(tmp_path: Path) -> None:
    diff = SyncPlanner().plan(
        _context(tmp_path, adapters=(_OwnedFileAdapter(content=b""),))
    ).diff_text()

    assert diff == (
        "--- /dev/null\n"
        "+++ b/generated.json\n"
        "@@ -0,0 +0,0 @@\n"
        "\\ Empty file created\n"
    )


def test_diff_escapes_valid_utf8_control_bytes_without_raw_nul(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"user NUL: \x00\n")

    diff = SyncPlanner().plan(_context(tmp_path)).diff_text()

    assert diff.startswith("# Control-byte diff uses unambiguous \\xNN escapes; ")
    assert "user NUL: \\x00" in diff
    assert "\x00" not in diff


def test_diff_escapes_bidi_format_controls(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("visible\u202ereordered\n", encoding="utf-8")

    diff = SyncPlanner().plan(_context(tmp_path)).diff_text()

    assert diff.startswith("# Control-byte diff uses unambiguous \\xNN escapes; ")
    assert "visible\\xe2\\x80\\xaereordered" in diff
    assert "\u202e" not in diff


def test_plan_removes_only_managed_humanizer_policy(tmp_path: Path) -> None:
    provider_skill = tmp_path / "providers" / "humanizer" / "humanizer" / "SKILL.md"
    provider_skill.parent.mkdir(parents=True)
    provider_skill.write_bytes(b"provider installation remains\n")
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(
        b"# user prefix\n"
        b"<!-- skillcord:begin -->\n"
        b"humanizer.humanizer\n"
        b"explicitly requests prose humanization\n"
        b"<!-- skillcord:end -->\n"
        b"user suffix\n"
    )

    plan = SyncPlanner().plan(_context(tmp_path))

    after = plan.changes[0].after
    assert b"humanizer.humanizer" not in after
    assert b"explicitly requests prose humanization" not in after
    assert after.startswith(b"# user prefix\n")
    assert after.endswith(b"user suffix\n")
    assert provider_skill.read_bytes() == b"provider installation remains\n"


def test_noninteractive_plan_fails_on_malformed_managed_block(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "<!-- skillcord:begin -->\nbroken\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="without end marker"):
        SyncPlanner().plan(replace(_context(tmp_path), non_interactive=True))


def test_noninteractive_plan_fails_on_missing_override_id(tmp_path: Path) -> None:
    decisions = OverrideConfig(
        schema_version=1,
        overrides={"testing": CapabilityOverride(prefer="ecc.missing")},
    )

    with pytest.raises(UnresolvedConflictError, match="ecc.missing"):
        SyncPlanner().plan(
            _context(
                tmp_path,
                decisions=decisions,
                discovered_skill_ids=frozenset({"superpowers.brainstorming"}),
                non_interactive=True,
            )
        )


class _OwnedFileAdapter:
    harness_id = "owned"

    def __init__(
        self,
        path: Path = Path("generated.json"),
        content: bytes = b'{"generated": true}\n',
    ) -> None:
        self.path = path
        self.content = content

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        return [
            GeneratedArtifact(
                path=self.path,
                ownership="owned_file",
                content=self.content,
                source_capability_ids=(),
            )
        ]


def test_noninteractive_plan_rejects_unknown_owned_file_content(tmp_path: Path) -> None:
    (tmp_path / "generated.json").write_bytes(b"user content\n")

    with pytest.raises(OwnedFileConflictError, match="generated.json"):
        SyncPlanner().plan(
            _context(
                tmp_path,
                adapters=(_OwnedFileAdapter(),),
                non_interactive=True,
            )
        )


def test_noninteractive_plan_allows_ordinary_overlap_with_keep_all(tmp_path: Path) -> None:
    candidates = (
        _skill(tmp_path, "superpowers", "test-driven-development"),
        _skill(tmp_path, "ecc", "tdd"),
    )
    group = CapabilityGroup(
        capability_id="testing",
        candidates=list(candidates),
    )

    plan = SyncPlanner().plan(
        _context(
            tmp_path,
            active_skills=candidates,
            conflict_groups=(group,),
            non_interactive=True,
        )
    )

    assert plan.changes


def test_noninteractive_keep_all_requires_every_candidate_active(tmp_path: Path) -> None:
    candidates = (
        _skill(tmp_path, "superpowers", "test-driven-development"),
        _skill(tmp_path, "ecc", "tdd"),
    )
    group = CapabilityGroup(capability_id="testing", candidates=list(candidates))

    with pytest.raises(UnresolvedConflictError, match="active skills do not match"):
        SyncPlanner().plan(
            _context(
                tmp_path,
                active_skills=(candidates[0],),
                conflict_groups=(group,),
                non_interactive=True,
            )
        )


def test_noninteractive_preference_rejects_extra_active_owner(tmp_path: Path) -> None:
    candidates = (
        _skill(tmp_path, "provider_a", "security"),
        _skill(tmp_path, "provider_b", "security"),
    )
    group = CapabilityGroup(
        capability_id="security",
        candidates=list(candidates),
        single_owner_required=True,
    )
    decisions = OverrideConfig(
        schema_version=1,
        overrides={"security": CapabilityOverride(prefer=candidates[0].normalized_id)},
    )

    with pytest.raises(UnresolvedConflictError, match="active skills do not match"):
        SyncPlanner().plan(
            _context(
                tmp_path,
                active_skills=candidates,
                decisions=decisions,
                conflict_groups=(group,),
                non_interactive=True,
            )
        )


def test_noninteractive_plan_rejects_unpersisted_ordinary_preference(tmp_path: Path) -> None:
    candidates = (
        _skill(tmp_path, "superpowers", "test-driven-development"),
        _skill(tmp_path, "ecc", "tdd"),
    )
    group = CapabilityGroup(
        capability_id="testing",
        candidates=list(candidates),
        action="prefer:superpowers.test-driven-development",
    )

    with pytest.raises(UnresolvedConflictError, match="testing"):
        SyncPlanner().plan(
            _context(
                tmp_path,
                active_skills=(candidates[0],),
                conflict_groups=(group,),
                non_interactive=True,
            )
        )


def test_override_validation_uses_discovered_conflict_candidates(tmp_path: Path) -> None:
    active = _skill(tmp_path, "superpowers", "test-driven-development")
    suppressed = _skill(tmp_path, "ecc", "tdd")
    group = CapabilityGroup(capability_id="testing", candidates=[active, suppressed])
    decisions = OverrideConfig(
        schema_version=1,
        overrides={
            "testing": CapabilityOverride(
                prefer=active.normalized_id,
                suppress=[suppressed.normalized_id],
            )
        },
    )

    plan = SyncPlanner().plan(
        _context(
            tmp_path,
            active_skills=(active,),
            decisions=decisions,
            conflict_groups=(group,),
            non_interactive=True,
        )
    )

    assert plan.changes


def test_noninteractive_plan_rejects_unresolved_single_owner_group(tmp_path: Path) -> None:
    group = CapabilityGroup(
        capability_id="security",
        candidates=[
            _skill(tmp_path, "provider_a", "security"),
            _skill(tmp_path, "provider_b", "security"),
        ],
        single_owner_required=True,
    )

    with pytest.raises(UnresolvedConflictError, match="security"):
        SyncPlanner().plan(
            _context(tmp_path, conflict_groups=(group,), non_interactive=True)
        )


def test_noninteractive_single_owner_action_must_be_persisted(tmp_path: Path) -> None:
    group = CapabilityGroup(
        capability_id="security",
        candidates=[
            _skill(tmp_path, "provider_a", "security"),
            _skill(tmp_path, "provider_b", "security"),
        ],
        action="prefer:provider_a.security",
        single_owner_required=True,
    )

    with pytest.raises(UnresolvedConflictError, match="security"):
        SyncPlanner().plan(
            _context(tmp_path, conflict_groups=(group,), non_interactive=True)
        )


@pytest.mark.parametrize(
    "target",
    [Path("..") / "escaped.md", Path("nested") / ".." / ".." / "escaped.md"],
)
def test_plan_rejects_artifact_targets_outside_project_root(
    tmp_path: Path,
    target: Path,
) -> None:
    with pytest.raises(UnsafeTargetPathError):
        SyncPlanner().plan(_context(tmp_path, adapters=(_OwnedFileAdapter(target),)))


def test_plan_rejects_absolute_artifact_target_outside_project_root(tmp_path: Path) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-absolute-escape.md"

    with pytest.raises(UnsafeTargetPathError):
        SyncPlanner().plan(_context(tmp_path, adapters=(_OwnedFileAdapter(target),)))


def test_plan_rejects_target_through_symlink_outside_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    with pytest.raises(UnsafeTargetPathError):
        SyncPlanner().plan(
            _context(tmp_path, adapters=(_OwnedFileAdapter(Path("linked/generated.json")),))
        )
