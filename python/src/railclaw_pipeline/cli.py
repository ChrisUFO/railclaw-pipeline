"""CLI interface for pipeline orchestrator."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from railclaw_pipeline.state.models import (
    CycleState,
    PipelineStage,
    PipelineState,
    PipelineStatus,
    Timestamps,
)
from railclaw_pipeline.state.persistence import load_state, save_state
from railclaw_pipeline.state.pid import (
    is_pid_alive,
    kill_pid,
    read_pid,
    remove_pid,
    write_pid,
)


def get_state_path() -> Path:
    state_dir = os.environ.get("RAILCLAW_STATE_DIR", ".pipeline-state")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / state_dir / "state.json"


def get_pid_path() -> Path:
    state_dir = os.environ.get("RAILCLAW_STATE_DIR", ".pipeline-state")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / state_dir / "pipeline.pid"


def get_events_path() -> Path:
    events_dir = os.environ.get("RAILCLAW_EVENTS_DIR", ".pipeline-events")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / events_dir / "events.jsonl"


def output_result(data: dict[str, Any]) -> None:
    print(json.dumps(data, default=str))


def _resolve_config_paths(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
) -> tuple[str, str, Path, Path]:
    if repo_path:
        os.environ["RAILCLAW_REPO_PATH"] = repo_path
    if factory_path:
        os.environ["RAILCLAW_FACTORY_PATH"] = factory_path
    if state_dir:
        os.environ["RAILCLAW_STATE_DIR"] = state_dir

    effective_repo = repo_path or os.environ.get("RAILCLAW_REPO_PATH", ".")
    effective_factory = factory_path or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")

    sd = state_dir or os.environ.get("RAILCLAW_STATE_DIR", ".pipeline-state")
    state_file = Path(effective_factory) / sd / "state.json"
    pid_file = Path(effective_factory) / sd / "pipeline.pid"

    return effective_repo, effective_factory, state_file, pid_file


def _run_pipeline_child(
    state_path: Path,
    pid_path: Path,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    from railclaw_pipeline.config import PipelineConfig
    from railclaw_pipeline.events.emitter import EventEmitter
    from railclaw_pipeline.pipeline import run_pipeline

    config = PipelineConfig(
        {
            "repoPath": repo_path,
            "factoryPath": factory_path,
        }
    )

    state = load_state(state_path)

    run_dir = (
        Path(factory_path)
        / ".pipeline-events"
        / "runs"
        / (
            f"issue-{state.issue_number}"
            if state.issue_number
            else f"manual-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
    )
    emitter = EventEmitter(config.events_path, run_dir=run_dir)

    try:
        asyncio.run(run_pipeline(state, config, emitter, hotfix=hotfix))
    except Exception as exc:
        with contextlib.suppress(FileNotFoundError):
            state = load_state(state_path)
        state.status = PipelineStatus.FAILED
        state.error = {"message": str(exc)}
        save_state(state, state_path)
    finally:
        emitter.close()
        remove_pid(pid_path)


def _detach_fork(
    state_path: Path,
    pid_path: Path,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    child_pid = os.fork()
    if child_pid > 0:
        write_pid(pid_path, child_pid)
        output_result(
            {
                "ok": True,
                "action": "run",
                "status": "started",
                "pid": child_pid,
                "message": "Pipeline started in background",
                "statePath": str(state_path),
            }
        )
        os._exit(0)

    os.setsid()

    second_pid = os.fork()
    if second_pid > 0:
        os._exit(0)

    sys.stdin.close()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno() if sys.stdin else devnull)
    os.close(devnull)

    write_pid(pid_path, os.getpid())

    try:
        state = load_state(state_path)
        state.pid = os.getpid()
        save_state(state, state_path)
    except Exception:
        pass

    _run_pipeline_child(state_path, pid_path, repo_path, factory_path, hotfix)


def _detach_subprocess(
    state_path: Path,
    pid_path: Path,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "railclaw_pipeline.cli",
        "_internal-run",
        "--state-path",
        str(state_path),
        "--pid-path",
        str(pid_path),
        "--repo-path",
        repo_path,
        "--factory-path",
        factory_path,
    ]
    if hotfix:
        cmd.append("--hotfix")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    write_pid(pid_path, proc.pid)

    try:
        state = load_state(state_path)
        state.pid = proc.pid
        save_state(state, state_path)
    except Exception:
        pass

    output_result(
        {
            "ok": True,
            "action": "run",
            "status": "started",
            "pid": proc.pid,
            "message": "Pipeline started in background",
            "statePath": str(state_path),
        }
    )


def _detach_and_run(
    state_path: Path,
    pid_path: Path,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    if hasattr(os, "fork"):
        _detach_fork(state_path, pid_path, repo_path, factory_path, hotfix)
    else:
        _detach_subprocess(state_path, pid_path, repo_path, factory_path, hotfix)


@click.group()
def main() -> None:
    pass


@main.command()
@click.option("--repo-path", type=str, help="Absolute path to the target repo")
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory (within the factory)")
@click.option("--issue", type=int, help="Issue number to process")
@click.option("--milestone", type=str, help="Milestone label for multi-issue mode")
@click.option("--hotfix", is_flag=True, help="Run in hotfix mode")
@click.option("--force-stage", type=str, help="Force start at specific stage")
@click.option("--detach", is_flag=True, help="Run as background daemon process")
def run(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    issue: int | None,
    milestone: str | None,
    hotfix: bool,
    force_stage: str | None,
    detach: bool,
) -> None:
    if not issue and not milestone:
        output_result({"ok": False, "action": "run", "error": "issue or milestone is required"})
        return

    effective_repo, effective_factory, state_path, pid_path = _resolve_config_paths(
        repo_path,
        factory_path,
        state_dir,
    )

    if state_path.exists():
        try:
            state = load_state(state_path)
            if state.status == PipelineStatus.RUNNING:
                pid = read_pid(pid_path)
                if pid and is_pid_alive(pid):
                    output_result(
                        {
                            "ok": False,
                            "action": "run",
                            "error": "Pipeline already running. Use 'resume' or 'abort' first.",
                        }
                    )
                    return
                else:
                    state.status = PipelineStatus.FAILED
                    state.error = {"message": "Previous pipeline died unexpectedly"}
                    save_state(state, state_path)
                    remove_pid(pid_path)
        except Exception:
            pass

    now = datetime.now(UTC)
    state = PipelineState(
        issue_number=issue or 0,
        milestone_mode=milestone is not None,
        milestone_label=milestone,
        stage=PipelineStage.STAGE0_PREFLIGHT if not force_stage else PipelineStage(force_stage),
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(
            started=now,
            stage_entered=now,
            last_updated=now,
        ),
        cycle=CycleState(),
    )

    save_state(state, state_path)

    if detach:
        _detach_and_run(state_path, pid_path, effective_repo, effective_factory, hotfix)
    else:
        from railclaw_pipeline.config import PipelineConfig
        from railclaw_pipeline.events.emitter import EventEmitter
        from railclaw_pipeline.pipeline import run_pipeline

        config = PipelineConfig(
            {
                "repoPath": effective_repo,
                "factoryPath": effective_factory,
            }
        )
        run_dir = (
            Path(effective_factory)
            / ".pipeline-events"
            / "runs"
            / (
                f"issue-{issue}"
                if issue
                else f"manual-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            )
        )
        emitter = EventEmitter(config.events_path, run_dir=run_dir)

        try:
            if hotfix:
                asyncio.run(run_pipeline(state, config, emitter, hotfix=True))
            else:
                asyncio.run(run_pipeline(state, config, emitter))

            state = load_state(state_path)
        except Exception as exc:
            with contextlib.suppress(FileNotFoundError):
                state = load_state(state_path)
            state.status = PipelineStatus.FAILED
            state.error = {"message": str(exc)}
            save_state(state, state_path)
        finally:
            emitter.close()

        output_result(
            {
                "ok": state.status == PipelineStatus.COMPLETED,
                "action": "run",
                "stage": state.stage.value,
                "status": state.status.value,
                "issueNumber": state.issue_number,
                "prNumber": state.pr_number,
                "branch": state.branch,
                "message": f"Pipeline {state.status.value}"
                + (f": {state.error['message']}" if state.error else ""),
                "statePath": str(state_path),
                "error": state.error.get("message") if state.error else None,
            }
        )


@main.command()
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory")
def status(factory_path: str | None, state_dir: str | None) -> None:
    _, effective_factory, state_path, pid_path = _resolve_config_paths(
        None,
        factory_path,
        state_dir,
    )
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({"ok": True, "action": "status", "message": "No active pipeline"})
        return

    if state.status == PipelineStatus.RUNNING:
        pid = read_pid(pid_path)
        if pid and not is_pid_alive(pid):
            state.status = PipelineStatus.FAILED
            state.error = {"message": "Pipeline process died unexpectedly"}
            state.timestamps.last_updated = datetime.now(UTC) if state.timestamps else None
            save_state(state, state_path)
            remove_pid(pid_path)

    output_result(
        {
            "ok": True,
            "action": "status",
            "stage": state.stage.value,
            "status": state.status.value,
            "issueNumber": state.issue_number,
            "prNumber": state.pr_number,
            "branch": state.branch,
            "pid": state.pid,
            "message": f"Pipeline {state.status.value} at {state.stage.value}",
            "statePath": str(state_path),
        }
    )


@main.command()
@click.option("--repo-path", type=str, help="Absolute path to the target repo")
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory")
@click.option("--force-stage", type=str, help="Force resume at specific stage")
@click.option("--detach", is_flag=True, help="Resume as background daemon process")
def resume(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    force_stage: str | None,
    detach: bool,
) -> None:
    effective_repo, effective_factory, state_path, pid_path = _resolve_config_paths(
        repo_path,
        factory_path,
        state_dir,
    )
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({"ok": False, "action": "resume", "error": "No pipeline state found"})
        return

    if force_stage:
        state.stage = PipelineStage(force_stage)

    state.status = PipelineStatus.RUNNING
    state.timestamps.last_updated = datetime.now(UTC) if state.timestamps else None

    save_state(state, state_path)

    if detach:
        _detach_and_run(state_path, pid_path, effective_repo, effective_factory, hotfix=False)
        return

    output_result(
        {
            "ok": True,
            "action": "resume",
            "stage": state.stage.value,
            "status": state.status.value,
            "issueNumber": state.issue_number,
            "message": f"Pipeline resumed at {state.stage.value}",
            "statePath": str(state_path),
        }
    )


@main.command()
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory")
def abort(factory_path: str | None, state_dir: str | None) -> None:
    _, effective_factory, state_path, pid_path = _resolve_config_paths(
        None,
        factory_path,
        state_dir,
    )
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({"ok": False, "action": "abort", "error": "No pipeline state found"})
        return

    pid = read_pid(pid_path)
    if pid:
        kill_pid(pid)
        remove_pid(pid_path)

    state.status = PipelineStatus.FAILED
    state.error = {"message": "Aborted by user"}

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(UTC)

    save_state(state, state_path)

    output_result(
        {
            "ok": True,
            "action": "abort",
            "stage": state.stage.value,
            "status": state.status.value,
            "issueNumber": state.issue_number,
            "message": "Pipeline aborted",
            "statePath": str(state_path),
        }
    )


@main.command()
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--max-age-days", type=int, default=30, help="Delete runs older than this many days")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show what would be deleted without deleting"
)
def cleanup(factory_path: str | None, max_age_days: int, dry_run: bool) -> None:
    from railclaw_pipeline.utils.cleanup import cleanup_old_runs

    effective_factory = factory_path or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    runs_dir = Path(effective_factory) / ".pipeline-events" / "runs"

    deleted = cleanup_old_runs(runs_dir, max_age_days, dry_run=dry_run)

    output_result(
        {
            "ok": True,
            "action": "cleanup",
            "dry_run": dry_run,
            "deleted": deleted,
            "deleted_count": len(deleted),
            "message": (
                f"{'Would delete' if dry_run else 'Deleted'} {len(deleted)} old run directories"
            ),
        }
    )


@main.command()
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--since", type=str, help="ISO8601 timestamp to filter notifications since")
@click.option("--limit", type=int, default=100, help="Max notifications to return")
def notifications(factory_path: str | None, since: str | None, limit: int) -> None:
    from railclaw_pipeline.events.notifications import query_notifications

    effective_factory = factory_path or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    os.environ["RAILCLAW_FACTORY_PATH"] = effective_factory

    results = query_notifications(since=since, limit=limit)

    output_result(
        {
            "ok": True,
            "action": "notifications",
            "count": len(results),
            "notifications": [n.__dict__ for n in results],
            "message": f"Returned {len(results)} notification(s)",
        }
    )


@main.command(hidden=True)
@click.option("--state-path", type=str, required=True)
@click.option("--pid-path", type=str, required=True)
@click.option("--repo-path", type=str, required=True)
@click.option("--factory-path", type=str, required=True)
@click.option("--hotfix", is_flag=True)
def _internal_run(
    state_path: str,
    pid_path: str,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    _run_pipeline_child(
        Path(state_path),
        Path(pid_path),
        repo_path,
        factory_path,
        hotfix,
    )


@main.command(hidden=True)
@click.option("--pid", "pid_num", type=int, required=True)
def _pid_check(pid_num: int) -> None:
    alive = is_pid_alive(pid_num)
    output_result({"alive": alive})


if __name__ == "__main__":
    main()
