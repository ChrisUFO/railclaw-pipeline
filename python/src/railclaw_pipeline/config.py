"""Pipeline configuration from environment and plugin config."""

import os
from pathlib import Path
from typing import Any


class PipelineConfig:
    """Pipeline configuration loaded from environment and plugin config."""

    def __init__(self, config_dict: dict[str, Any] | None = None) -> None:
        config_dict = config_dict or {}

        self.repo_path = Path(
            config_dict.get("repoPath") or os.environ.get("RAILCLAW_REPO_PATH", ".")
        )

        self.factory_path = Path(
            config_dict.get("factoryPath") or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
        )

        self.state_dir = config_dict.get("stateDir") or ".pipeline-state"
        self.events_dir = config_dict.get("eventsDir") or ".pipeline-events"

        self.state_path = self.factory_path / self.state_dir / "state.json"
        self.events_path = self.factory_path / self.events_dir / "events.jsonl"
        self.pid_path = self.factory_path / self.state_dir / "pipeline.pid"

        self.agents = config_dict.get(
            "agents",
            {
                "blueprint": {"model": "openai/gpt-5.4", "timeout": 600},
                "wrench": {"model": "zai/glm-5-turbo", "timeout": 1200},
                "scope": {"model": "minimax/MiniMax-M2.7", "timeout": 600},
                "beaker": {"model": "openai/gpt-5.4-mini", "timeout": 600},
                "wrenchSr": {"model": "gemini/gemini-3.1-pro-preview", "timeout": 1200},
            },
        )

        self.timing = config_dict.get(
            "timing",
            {
                "geminiPollInterval": 60,
                "approvalTimeout": 86400,
                "healthCheckTimeout": 30,
            },
        )

        self.pm2 = config_dict.get(
            "pm2",
            {
                "processName": "railclaw-mc",
                "ecosystemPath": "ecosystem.config.cjs",
            },
        )

        self.escalation = config_dict.get(
            "escalation",
            {
                "wrenchSrAfterRound": 3,
                "chrisAfterRound": 5,
            },
        )

    def get_agent_timeout(self, agent: str) -> int:
        """Get timeout for specific agent."""
        agent_config = self.agents.get(agent, {})
        return agent_config.get("timeout", 600)

    def get_agent_model(self, agent: str) -> str:
        """Get model for specific agent."""
        agent_config = self.agents.get(agent, {})
        return agent_config.get("model", "")
