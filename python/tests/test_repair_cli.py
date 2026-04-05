"""Tests for repair CLI command."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from railclaw_pipeline.cli import main
from railclaw_pipeline.validation.repair import (
    RepairEngine,
)


class TestRepairCLI:
    def test_repair_command_exists(self) -> None:
        result = CliRunner().invoke(main, ["repair", "--help"])
        assert result.exit_code == 0
        assert "--fix" in result.output
        assert "--force" in result.output

    def test_repair_dry_run_reports_issues(self, tmp_path: Path) -> None:
        """Without --fix, repair should scan and report only."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        state_path.write_text("not json {{{")

        result = CliRunner().invoke(
            main,
            [
                "repair",
                "--repo-path",
                str(repo),
                "--factory-path",
                str(factory),
            ],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is True
        assert output["action"] == "repair"
        assert output["fix_mode"] is False
        assert output["result"]["issue_count"] >= 1

    def test_repair_fix_mode(self, tmp_path: Path) -> None:
        """With --fix, repair should auto-fix safe issues."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        state_path.write_text("not json {{{")
        lock_path = state_dir / "pipeline.lock"
        lock_path.write_text(
            json.dumps(
                {
                    "pid": 999999999,
                    "timestamp": "2025-01-01T00:00:00",
                    "agent": "dead",
                    "stage": "stage1",
                    "run_id": "issue-1",
                }
            )
        )

        result = CliRunner().invoke(
            main,
            [
                "repair",
                "--repo-path",
                str(repo),
                "--factory-path",
                str(factory),
                "--fix",
                "--force",
            ],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is True
        assert output["fix_mode"] is True
        assert output["result"]["fixed_count"] >= 1

    def test_repair_force_mode(self, tmp_path: Path) -> None:
        """With --force, repair should fix dangerous issues too."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        state_path.write_text("not json {{{")
        lock_path = state_dir / "pipeline.lock"
        lock_path.write_text(
            json.dumps(
                {
                    "pid": 999999999,
                    "timestamp": "2025-01-01T00:00:00",
                    "agent": "dead",
                    "stage": "stage1",
                    "run_id": "issue-1",
                }
            )
        )

        result = CliRunner().invoke(
            main,
            [
                "repair",
                "--repo-path",
                str(repo),
                "--factory-path",
                str(factory),
                "--fix",
                "--force",
            ],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is True
        assert output["result"]["fixed_count"] >= 1

    def test_repair_clean_state(self, tmp_path: Path) -> None:
        """Repair on clean state should report no issues."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "issue_number": 1,
                    "stage": "stage1_blueprint",
                    "status": "running",
                }
            )
        )

        result = CliRunner().invoke(
            main,
            [
                "repair",
                "--repo-path",
                str(repo),
                "--factory-path",
                str(factory),
            ],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is True
        assert output["result"]["issue_count"] == 0


class TestRepairEngineOrphanedBranchFix:
    async def test_fix_orphaned_branch_passes_branch_name(self, tmp_path: Path) -> None:
        """_fix_orphaned_branch should receive the branch name as argument."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()
        state_path = state_dir / "state.json"
        state_path.write_text(
            json.dumps({"issue_number": 1, "stage": "stage1_blueprint", "status": "running"})
        )

        engine = RepairEngine(
            repo_path=repo,
            factory_path=factory,
            state_path=state_path,
            lock_path=state_dir / "pipeline.lock",
            state_dir=state_dir,
        )

        async def mock_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            cmd_str = " ".join(str(a) for a in args)
            if "branch" in cmd_str and "--list" in cmd_str:
                proc.communicate = AsyncMock(return_value=(b"  feat/issue-42\n", b""))
            elif "pr" in cmd_str and "list" in cmd_str:
                proc.communicate = AsyncMock(return_value=(b"[]", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await engine.scan()

        orphaned = [i for i in result.issues if i.category == "orphaned_branch"]
        assert len(orphaned) == 1
        assert orphaned[0].detail == "feat/issue-42"

        del_proc = AsyncMock()
        del_proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=del_proc):
            await engine._fix_orphaned_branch("feat/issue-42")
