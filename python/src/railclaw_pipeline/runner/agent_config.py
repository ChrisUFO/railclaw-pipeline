"""Shared agent config builder used by pipeline and cycle2 stages."""

from __future__ import annotations

from pathlib import Path

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.runner.agent import AgentConfig


def get_agent_config(config: PipelineConfig, agent_name: str) -> AgentConfig:
    """Build AgentConfig for a named agent.

    Centralised so pipeline.py and cycle2_gemini.py stay in sync automatically.
    """
    agent_cfg = config.agents.get(agent_name, {})
    model = agent_cfg.get("model", "")
    timeout = agent_cfg.get("timeout", 600)

    workdir = config.repo_path
    command = "opencode"
    args_template = ["run", "--dir", str(workdir), "{prompt}"]

    if agent_name in ("wrenchSr", "scout"):
        command = "gemini"
        args_template = ["--model", model, "{prompt}"]
    elif agent_name in ("blueprint", "wrench", "scope", "beaker", "quill"):
        env_dir = config.factory_path / "envs" / agent_name
        if env_dir.exists():
            workdir = env_dir
        args_template = ["run", "--dir", str(workdir), "{prompt}"]

    return AgentConfig(
        name=agent_name,
        model=model,
        timeout=timeout,
        command=command,
        args_template=args_template,
        workdir=workdir,
    )
