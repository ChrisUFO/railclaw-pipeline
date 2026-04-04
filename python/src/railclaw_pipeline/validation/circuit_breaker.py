"""Circuit breaker for agent timeouts.

Tracks consecutive timeouts per agent and prevents retry cascades.
After 2 consecutive timeouts for the same agent, the circuit opens
and the pipeline escalates immediately instead of retrying.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from railclaw_pipeline.utils.atomic_write import atomic_write

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_THRESHOLD = 2


@dataclass
class AgentState:
    """Tracking state for a single agent."""

    consecutive_timeouts: int = 0
    last_timeout: str = ""


@dataclass
class CircuitBreakerState:
    """Full circuit breaker state."""

    agents: dict[str, AgentState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            agent: {
                "consecutive_timeouts": state.consecutive_timeouts,
                "last_timeout": state.last_timeout,
            }
            for agent, state in self.agents.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CircuitBreakerState":
        state = cls()
        for agent, info in data.items():
            state.agents[agent] = AgentState(
                consecutive_timeouts=info.get("consecutive_timeouts", 0),
                last_timeout=info.get("last_timeout", ""),
            )
        return state


class CircuitBreaker:
    """Circuit breaker that tracks consecutive agent timeouts.

    After `threshold` consecutive timeouts for the same agent,
    the circuit is open and retries should be skipped in favor
    of escalation.
    """

    def __init__(
        self,
        state_path: Path,
        threshold: int = DEFAULT_TIMEOUT_THRESHOLD,
    ) -> None:
        self.state_path = state_path
        self.threshold = threshold
        self._state = self._load()

    def record_timeout(self, agent: str) -> None:
        """Record a timeout for the given agent."""
        if agent not in self._state.agents:
            self._state.agents[agent] = AgentState()
        self._state.agents[agent].consecutive_timeouts += 1
        self._state.agents[agent].last_timeout = datetime.now(UTC).isoformat()
        self._save()

    def record_success(self, agent: str) -> None:
        """Record a success, resetting the consecutive timeout counter."""
        if agent in self._state.agents:
            self._state.agents[agent].consecutive_timeouts = 0
            self._save()

    def is_open(self, agent: str) -> bool:
        """Check if the circuit is open for the given agent."""
        if agent not in self._state.agents:
            return False
        return self._state.agents[agent].consecutive_timeouts >= self.threshold

    def get_consecutive_timeouts(self, agent: str) -> int:
        """Get the current consecutive timeout count for an agent."""
        if agent not in self._state.agents:
            return 0
        return self._state.agents[agent].consecutive_timeouts

    def reset(self, agent: str | None = None) -> None:
        """Reset circuit breaker state. If agent is None, reset all."""
        if agent:
            if agent in self._state.agents:
                del self._state.agents[agent]
        else:
            self._state = CircuitBreakerState()
        self._save()

    def _load(self) -> CircuitBreakerState:
        if not self.state_path.exists():
            return CircuitBreakerState()
        try:
            data = json.loads(self.state_path.read_text())
            return CircuitBreakerState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return CircuitBreakerState()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._state.to_dict(), indent=2)
        if not atomic_write(self.state_path, content):
            logger.warning("Failed to persist circuit breaker state to %s", self.state_path)
