"""CLI interface for pipeline orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal as signal_lib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from railclaw_pipeline.state.lock import StateLock, StateLockError
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

logger = logging.getLogger(__name__)


def _resolve_factory_path() -> tuple[str, str]:
    """Resolve factory and state dir from environment variables."""
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    state_dir = os.environ.get("RAILCLAW_STATE_DIR", ".pipeline-state")
    return factory_path, state_dir


def get_state_path() -> Path:
    factory_path, state_dir = _resolve_factory_path()
    return Path(factory_path) / state_dir / "state.json"


def get_pid_path() -> Path:
    factory_path, state_dir = _resolve_factory_path()
    return Path(factory_path) / state_dir / "pipeline.pid"


def get_events_path() -> Path:
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    events_dir = os.environ.get("RAILCLAW_EVENTS_DIR", ".pipeline-events")
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


def _build_run_dir(factory_path: str, issue: int | None) -> Path:
    """Build the run log directory path for a given issue or manual run."""
    return (
        Path(factory_path)
        / ".pipeline-events"
        / "runs"
        / (f"issue-{issue}" if issue else f"manual-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    )


def _build_preflight_gate(config: PipelineConfig) -> PreflightGate:
    """Build a PreflightGate from a PipelineConfig."""
    from railclaw_pipeline.validation.preflight import PreflightGate

    return PreflightGate(
        repo_path=config.repo_path,
        factory_path=config.factory_path,
        state_path=config.state_path,
        lock_path=config.lock_path,
        lock_max_age=config.lock_max_age,
        disk_space_min_mb=config.preflight.get("diskSpaceMinMB", 500),
        agent_commands=config.preflight.get("agentCommands"),
    )


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

    if hasattr(signal_lib, "SIGTERM"):

        def _sigterm_handler(_signum: int, _frame: Any) -> None:
            logger.info("Received SIGTERM, shutting down daemon gracefully")
            raise SystemExit(128 + _signum)

        signal_lib.signal(signal_lib.SIGTERM, _sigterm_handler)

    config = PipelineConfig(
        {
            "repoPath": repo_path,
            "factoryPath": factory_path,
        }
    )

    state = load_state(state_path)

    # Pre-flight validation in child process (detached mode) — defense in depth.
    # The parent process also runs preflight before forking, but this ensures
    # validation even if the parent was bypassed or started with --skip-preflight.
    # Only runs on fresh starts (stage0_preflight); resumed runs skip this.
    if state.stage.value == "stage0_preflight":
        gate = _build_preflight_gate(config)
        preflight_result = asyncio.run(gate.run())
        if not preflight_result.passed:
            state.status = PipelineStatus.FAILED
            state.error = {
                "message": f"Pre-flight checks failed ({preflight_result.failure_count} issue(s)).",
                "preflight": preflight_result.to_dict(),
            }
            save_state(state, state_path)
            logger.error("Pre-flight validation failed in child process")
            return

    run_dir = _build_run_dir(factory_path, state.issue_number)
    emitter = EventEmitter(config.events_path, run_dir=run_dir)

    lock = StateLock(config.lock_path, max_age=config.lock_max_age)
    try:
        lock.acquire(
            agent="pipeline",
            stage=state.stage.value,
            run_id=f"issue-{state.issue_number}",
        )
    except StateLockError as exc:
        logger.error("Failed to acquire lock in child process: %s", exc)
        state.status = PipelineStatus.FAILED
        state.error = {"message": f"Lock acquisition failed: {exc}"}
        save_state(state, state_path)
        return

    try:
        asyncio.run(run_pipeline(state, config, emitter, hotfix=hotfix))
    except BaseException as exc:
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            logger.info("Daemon interrupted, saving FAILED state")
        with contextlib.suppress(FileNotFoundError):
            state = load_state(state_path)
        state.status = PipelineStatus.FAILED
        state.error = {"message": str(exc)}
        save_state(state, state_path)
    finally:
        emitter.close()
        remove_pid(pid_path)
        lock.release()


def _detach_fork(
    state_path: Path,
    pid_path: Path,
    repo_path: str,
    factory_path: str,
    hotfix: bool,
) -> None:
    child_pid = os.fork()
    if child_pid > 0:
        os.waitpid(child_pid, 0)
        os._exit(0)

    os.setsid()

    grandchild_pid = os.fork()
    if grandchild_pid > 0:
        output_result(
            {
                "ok": True,
                "action": "run",
                "status": "started",
                "pid": grandchild_pid,
                "message": "Pipeline started in background",
                "statePath": str(state_path),
            }
        )
        os._exit(0)

    devnull_fd = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull_fd, fd)
    os.close(devnull_fd)

    write_pid(pid_path, os.getpid())

    try:
        state = load_state(state_path)
        state.pid = os.getpid()
        save_state(state, state_path)
    except Exception as exc:
        logger.error("Failed to persist PID to state: %s", exc)

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
    startupinfo = None
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )

    write_pid(pid_path, proc.pid)

    try:
        state = load_state(state_path)
        state.pid = proc.pid
        save_state(state, state_path)
    except Exception as exc:
        logger.error("Failed to persist PID to state: %s", exc)

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
@click.option("--skip-preflight", is_flag=True, help="Skip pre-flight validation checks")
def run(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    issue: int | None,
    milestone: str | None,
    hotfix: bool,
    force_stage: str | None,
    detach: bool,
    skip_preflight: bool,
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
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            pass

    if not skip_preflight:
        from railclaw_pipeline.config import PipelineConfig

        preflight_config = PipelineConfig(
            {
                "repoPath": effective_repo,
                "factoryPath": effective_factory,
            }
        )
        gate = _build_preflight_gate(preflight_config)
        result = asyncio.run(gate.run())
        if not result.passed:
            output_result(
                {
                    "ok": False,
                    "action": "run",
                    "error": f"Pre-flight checks failed ({result.failure_count} issue(s)).",
                    "preflight": result.to_dict(),
                }
            )
            return

    now = datetime.now(UTC)
    state = PipelineState(
        issue_number=issue or 0,
        milestone_mode=milestone is not None,
        milestone_label=milestone,
        stage=PipelineStage(force_stage) if force_stage else PipelineStage.STAGE0_PREFLIGHT,
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(started=now, stage_entered=now, last_updated=now),
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

        lock = StateLock(pid_path.parent / "pipeline.lock", max_age=config.lock_max_age)
        try:
            lock.acquire(
                agent="pipeline",
                stage=state.stage.value,
                run_id=f"issue-{state.issue_number}",
            )
        except StateLockError as exc:
            output_result({"ok": False, "action": "run", "error": str(exc)})
            return

        run_dir = _build_run_dir(effective_factory, issue)
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
            if lock:
                lock.release()

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
            if not state.timestamps:
                state.timestamps = Timestamps(
                    started=datetime.now(UTC),
                    stage_entered=datetime.now(UTC),
                    last_updated=datetime.now(UTC),
                )
            else:
                state.timestamps.last_updated = datetime.now(UTC)
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
@click.option(
    "--skip-preflight",
    is_flag=True,
    help="Reserved for API symmetry (preflight is always skipped on resume)",
)
def resume(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    force_stage: str | None,
    detach: bool,
    skip_preflight: bool,
) -> None:
    # Note: skip_preflight is accepted for API symmetry but has no effect.
    # Resume always skips preflight since it was validated on the initial run.
    _ = skip_preflight
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

    from railclaw_pipeline.config import PipelineConfig
    from railclaw_pipeline.events.emitter import EventEmitter
    from railclaw_pipeline.pipeline import run_pipeline

    config = PipelineConfig(
        {
            "repoPath": effective_repo,
            "factoryPath": effective_factory,
        }
    )

    lock = StateLock(pid_path.parent / "pipeline.lock", max_age=config.lock_max_age)
    try:
        lock.acquire(
            agent="pipeline",
            stage=state.stage.value,
            run_id=f"issue-{state.issue_number}",
        )
    except StateLockError as exc:
        output_result({"ok": False, "action": "resume", "error": str(exc)})
        return

    run_dir = _build_run_dir(effective_factory, state.issue_number)
    emitter = EventEmitter(config.events_path, run_dir=run_dir)

    try:
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
        lock.release()

    output_result(
        {
            "ok": state.status == PipelineStatus.COMPLETED,
            "action": "resume",
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
    from dataclasses import asdict

    from railclaw_pipeline.events.notifications import query_notifications

    effective_factory = factory_path or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    os.environ["RAILCLAW_FACTORY_PATH"] = effective_factory

    results = query_notifications(since=since, limit=limit)

    output_result(
        {
            "ok": True,
            "action": "notifications",
            "count": len(results),
            "notifications": [asdict(n) for n in results],
            "message": f"Returned {len(results)} notification(s)",
        }
    )


@main.command()
@click.option("--repo-path", type=str, help="Absolute path to the target repo")
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory")
@click.option("--fix", is_flag=True, help="Auto-fix all safe issues")
@click.option("--force", is_flag=True, help="Force fix dangerous issues")
def repair(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    fix: bool,
    force: bool,
) -> None:
    effective_repo, effective_factory, state_path, pid_path = _resolve_config_paths(
        repo_path,
        factory_path,
        state_dir,
    )

    from railclaw_pipeline.validation.repair import RepairEngine

    engine = RepairEngine(
        repo_path=Path(effective_repo),
        factory_path=Path(effective_factory),
        state_path=state_path,
        lock_path=pid_path.parent / "pipeline.lock",
        state_dir=pid_path.parent,
    )

    if fix:
        result = asyncio.run(engine.repair(force=force))
    else:
        result = asyncio.run(engine.scan())
        result.unfixable.extend(
            f"[{i.category}] {i.description}" for i in result.issues if not i.fixable
        )

    output_result(
        {
            "ok": True,
            "action": "repair",
            "fix_mode": fix,
            "result": result.to_dict(),
            "message": (
                f"Found {result.issue_count} issue(s)"
                + (f", fixed {len(result.fixed)}" if fix else "")
                + (f", {len(result.unfixable)} unfixable" if result.unfixable else "")
            ),
        }
    )


@main.command("_internal-run", hidden=True)
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
