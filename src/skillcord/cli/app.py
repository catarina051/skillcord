"""Typer command orchestration for the Skillcord V1 local runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from skillcord.adapters.base import AdapterContext, HarnessAdapter
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.adapters.codex import CodexAdapter
from skillcord.cli.rendering import render_conflicts, render_history, render_scan, render_status
from skillcord.decisions.store import DecisionStore
from skillcord.discovery.service import DiscoveryResult, DiscoveryService
from skillcord.history.store import HistoryEntry, HistoryStore
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.status import StatusReport
from skillcord.overlap.aliases import AliasRegistry
from skillcord.overlap.grouping import group_capabilities
from skillcord.overlap.resolution import ConflictResolver
from skillcord.status.doctor import Doctor, DoctorContext
from skillcord.status.serializer import serialize_status
from skillcord.sync.applier import ApplyResult, SyncApplier
from skillcord.sync.planner import SyncContext, SyncPlan, SyncPlanError, SyncPlanner

app = typer.Typer(no_args_is_help=True, add_completion=False)
PROJECT_ROOT_OPTION = typer.Option(Path("."), "--project-root", file_okay=False, resolve_path=True)
SOURCES_OPTION = typer.Option(None, "--source")
HARNESSES_OPTION = typer.Option(None, "--harness")
YES_OPTION = typer.Option(False, "--yes")
NON_INTERACTIVE_OPTION = typer.Option(False, "--non-interactive")
JSON_OPTION = typer.Option(False, "--json")


class CLIUsageError(ValueError):
    """A config or option error that maps to the standard CLI usage exit code."""


@dataclass(frozen=True)
class RuntimeServices:
    """Injectable orchestration dependencies; commands contain no domain decisions."""

    discovery: DiscoveryService
    planner: SyncPlanner
    applier: SyncApplier
    doctor: Doctor

    @classmethod
    def default(cls, project_root: Path) -> RuntimeServices:
        return cls(
            discovery=DiscoveryService.default(),
            planner=SyncPlanner(),
            applier=SyncApplier(history_store=HistoryStore(project_root / ".ai" / "history.jsonl")),
            doctor=Doctor(),
        )


def _project_path(root: Path) -> Path:
    return root / ".ai" / "project.yaml"


def _load_project(root: Path) -> ProjectConfig:
    path = _project_path(root)
    if not path.is_file():
        raise CLIUsageError(f"project configuration is missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ProjectConfig.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise CLIUsageError(f"project configuration is invalid: {path}") from error


def _adapters_for(project: ProjectConfig) -> tuple[HarnessAdapter, ...]:
    available: Mapping[str, HarnessAdapter] = {"claude": ClaudeAdapter(), "codex": CodexAdapter()}
    unknown = sorted(set(project.ai.harnesses) - available.keys())
    if unknown:
        raise CLIUsageError(f"unsupported V1 harnesses: {', '.join(unknown)}")
    return tuple(available[harness] for harness in project.ai.harnesses)


def _source_roots(values: Sequence[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        provider_id, separator, raw_path = value.partition("=")
        if not separator or not provider_id or not raw_path or provider_id in roots:
            raise CLIUsageError("sources must be unique provider_id=path values")
        roots[provider_id] = Path(raw_path)
    return roots


def _scan(root: Path, sources: Sequence[str], services: RuntimeServices) -> DiscoveryResult:
    return services.discovery.scan(root, _source_roots(sources))


def _load_overrides(root: Path) -> tuple[DecisionStore, OverrideConfig]:
    """Load persisted decisions, translating only malformed local schema state."""

    store = DecisionStore(root / ".ai" / "overrides.yaml")
    try:
        return store, store.load()
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise CLIUsageError(f"override configuration is invalid: {store.path}") from error


def _sync_context(
    root: Path,
    sources: Sequence[str],
    non_interactive: bool,
    services: RuntimeServices,
) -> SyncContext:
    project = _load_project(root)
    discovered = _scan(root, sources, services)
    _, overrides = _load_overrides(root)
    skills = tuple(skill for provider in discovered.providers for skill in provider.skills)
    groups = tuple(group_capabilities(skills, AliasRegistry({}), overrides))
    return SyncContext(
        project_root=root,
        adapter_context=AdapterContext(project=project, active_skills=skills, decisions=overrides),
        adapters=_adapters_for(project),
        discovered_skill_ids=frozenset(skill.normalized_id for skill in skills),
        conflict_groups=groups,
        non_interactive=non_interactive,
    )


def run_init(root: Path, harnesses: Sequence[str]) -> None:
    """Create only the V1 project configuration, without probing or installing anything."""

    if not harnesses:
        raise CLIUsageError("at least one --harness is required")
    project = ProjectConfig(
        schema_version=1,
        project=ProjectInfo(intent="AI runtime configuration"),
        ai=AIConfig(harnesses=list(dict.fromkeys(harnesses))),
    )
    path = _project_path(root)
    if path.exists():
        raise CLIUsageError(f"project configuration already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(project.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def run_conflicts(
    root: Path,
    sources: Sequence[str],
    decisions: Mapping[str, str],
    services: RuntimeServices,
) -> str:
    context = _sync_context(root, sources, False, services)
    resolution = ConflictResolver().resolve(context.conflict_groups, decisions)
    store, _ = _load_overrides(root)
    for group in resolution.groups:
        action = decisions.get(group.capability_id)
        if action is None:
            continue
        if action == "keep-all":
            store.save_decision(group.capability_id, None, [])
            continue
        prefix = "prefer:"
        if not action.startswith(prefix):
            raise CLIUsageError(f"invalid conflict action for {group.capability_id}: {action}")
        preferred = action.removeprefix(prefix)
        candidate_ids = {candidate.normalized_id for candidate in group.candidates}
        if preferred not in candidate_ids:
            raise CLIUsageError(f"invalid conflict owner for {group.capability_id}: {preferred}")
        store.save_decision(group.capability_id, preferred, sorted(candidate_ids - {preferred}))
    return render_conflicts(resolution.groups, ConflictResolver().question_budget)


def run_sync(
    root: Path,
    sources: Sequence[str],
    non_interactive: bool,
    approved: bool,
    services: RuntimeServices,
) -> tuple[SyncPlan, ApplyResult]:
    plan = services.planner.plan(_sync_context(root, sources, non_interactive, services))
    return plan, services.applier.apply(plan, approved=approved)


def run_doctor(root: Path, sources: Sequence[str], services: RuntimeServices) -> StatusReport:
    try:
        project = _load_project(root)
        project_error = None
    except CLIUsageError as error:
        project = None
        project_error = str(error)
    discovered = _scan(root, sources, services)
    _, overrides = _load_overrides(root)
    skills = tuple(skill for provider in discovered.providers for skill in provider.skills)
    return services.doctor.run(
        DoctorContext(
            project=project,
            project_error=project_error,
            providers=discovered.providers,
            harnesses=discovered.harnesses,
            overrides=overrides,
            conflict_groups=group_capabilities(skills, AliasRegistry({}), overrides),
            project_root=root,
        )
    )


def run_history(root: Path) -> list[HistoryEntry]:
    return HistoryStore(root / ".ai" / "history.jsonl").list()


def _exit_for_error(error: Exception) -> int:
    if isinstance(error, CLIUsageError):
        return 2
    if isinstance(error, SyncPlanError):
        return 1
    return 3


def _command_services(root: Path) -> RuntimeServices:
    return RuntimeServices.default(root)


@app.command("init")
def init_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    harness: list[str] = HARNESSES_OPTION,
) -> None:
    try:
        run_init(project_root, harness or ())
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error
    typer.echo(f"Initialized {_project_path(project_root)}")


@app.command("scan")
def scan_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
) -> None:
    try:
        typer.echo(render_scan(_scan(project_root, source or (), _command_services(project_root))), nl=False)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error


@app.command("conflicts")
def conflicts_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
) -> None:
    try:
        services = _command_services(project_root)
        context = _sync_context(project_root, source or (), False, services)
        resolution = ConflictResolver().resolve(context.conflict_groups, {})
        decisions: dict[str, str] = {}
        for group in resolution.prompted_groups:
            typer.echo(f"{group.capability_id}: 0 keep-all")
            for index, candidate in enumerate(group.candidates, start=1):
                typer.echo(f"{index} prefer {candidate.normalized_id}")
            selected = typer.prompt("Select", default="0")
            try:
                choice = int(selected)
            except ValueError as error:
                raise CLIUsageError("conflict selection must be a number") from error
            if choice == 0:
                decisions[group.capability_id] = "keep-all"
            elif 1 <= choice <= len(group.candidates):
                decisions[group.capability_id] = f"prefer:{group.candidates[choice - 1].normalized_id}"
            else:
                raise CLIUsageError("conflict selection is out of range")
        typer.echo(run_conflicts(project_root, source or (), decisions, services), nl=False)
    except CLIUsageError as error:
        # An uninitialized project has no persisted conflicts and is still inspectable.
        if not _project_path(project_root).exists():
            typer.echo(render_conflicts((), ConflictResolver().question_budget), nl=False)
        else:
            typer.echo(str(error), err=True)
            raise typer.Exit(2) from error


@app.command("diff")
def diff_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
) -> None:
    try:
        context = _sync_context(project_root, source or (), False, _command_services(project_root))
        typer.echo(_command_services(project_root).planner.plan(context).diff_text(), nl=False)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error


@app.command("sync")
def sync_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
    yes: bool = YES_OPTION,
    non_interactive: bool = NON_INTERACTIVE_OPTION,
) -> None:
    services = _command_services(project_root)
    try:
        if non_interactive and not yes:
            raise CLIUsageError("--non-interactive requires explicit --yes approval")
        context = _sync_context(project_root, source or (), non_interactive, services)
        plan = services.planner.plan(context)
        if plan.changes:
            typer.echo(plan.diff_text(), nl=False)
        approved = yes or typer.confirm("Apply this plan?", default=False)
        result = services.applier.apply(plan, approved)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error
    typer.echo("Applied sync." if result.applied else "Sync not applied.")


@app.command("doctor")
def doctor_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
) -> None:
    try:
        report = run_doctor(project_root, source or (), _command_services(project_root))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error
    typer.echo(render_status(report), nl=False)
    if not report.ready:
        raise typer.Exit(1)


@app.command("status")
def status_command(
    project_root: Path = PROJECT_ROOT_OPTION,
    source: list[str] = SOURCES_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    try:
        report = run_doctor(project_root, source or (), _command_services(project_root))
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error
    typer.echo(serialize_status(report) if json_output else render_status(report), nl=False)
    if not report.ready:
        raise typer.Exit(1)


@app.command("history")
def history_command(
    project_root: Path = PROJECT_ROOT_OPTION,
) -> None:
    try:
        typer.echo(render_history(run_history(project_root)), nl=False)
    except Exception as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(_exit_for_error(error)) from error
