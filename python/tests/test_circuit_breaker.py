"""Tests for circuit breaker — timeout tracking and escalation."""

import json
from pathlib import Path

import pytest

from railclaw_pipeline.validation.circuit_breaker import (
    AgentState,
    CircuitBreaker,
    CircuitBreakerState,
)


class TestCircuitBreakerState:
    def test_defaults(self) -> None:
        state = CircuitBreakerState()
        assert state.agents == {}

    def test_to_dict_empty(self) -> None:
        state = CircuitBreakerState()
        assert state.to_dict() == {}

    def test_to_dict_with_agents(self) -> None:
        state = CircuitBreakerState()
        state.agents["wrench"] = AgentState(
            consecutive_timeouts=2, last_timeout="2025-01-01T00:00:00"
        )
        d = state.to_dict()
        assert d["wrench"]["consecutive_timeouts"] == 2

    def test_from_dict(self) -> None:
        data = {"scope": {"consecutive_timeouts": 1, "last_timeout": "ts"}}
        state = CircuitBreakerState.from_dict(data)
        assert state.agents["scope"].consecutive_timeouts == 1


class TestAgentState:
    def test_defaults(self) -> None:
        s = AgentState()
        assert s.consecutive_timeouts == 0
        assert s.last_timeout == ""


class TestCircuitBreaker:
    def test_initially_closed(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json")
        assert cb.is_open("wrench") is False

    def test_record_timeout_increments(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json")
        cb.record_timeout("wrench")
        assert cb.get_consecutive_timeouts("wrench") == 1
        cb.record_timeout("wrench")
        assert cb.get_consecutive_timeouts("wrench") == 2

    def test_opens_after_threshold(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json", threshold=2)
        cb.record_timeout("wrench")
        assert cb.is_open("wrench") is False
        cb.record_timeout("wrench")
        assert cb.is_open("wrench") is True

    def test_record_success_resets(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json", threshold=2)
        cb.record_timeout("wrench")
        cb.record_timeout("wrench")
        assert cb.is_open("wrench") is True
        cb.record_success("wrench")
        assert cb.is_open("wrench") is False
        assert cb.get_consecutive_timeouts("wrench") == 0

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "cb.json"
        cb1 = CircuitBreaker(path, threshold=2)
        cb1.record_timeout("wrench")
        cb1.record_timeout("wrench")

        cb2 = CircuitBreaker(path, threshold=2)
        assert cb2.is_open("wrench") is True

    def test_unknown_agent_not_open(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json")
        assert cb.is_open("unknown") is False
        assert cb.get_consecutive_timeouts("unknown") == 0

    def test_reset_single_agent(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json")
        cb.record_timeout("wrench")
        cb.record_timeout("scope")
        cb.reset("wrench")
        assert cb.get_consecutive_timeouts("wrench") == 0
        assert cb.get_consecutive_timeouts("scope") == 1

    def test_reset_all(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json")
        cb.record_timeout("wrench")
        cb.record_timeout("scope")
        cb.reset()
        assert cb.get_consecutive_timeouts("wrench") == 0
        assert cb.get_consecutive_timeouts("scope") == 0

    def test_corrupt_file_returns_empty_state(self, tmp_path: Path) -> None:
        path = tmp_path / "cb.json"
        path.write_text("not json {{{")
        cb = CircuitBreaker(path)
        assert cb.is_open("wrench") is False

    def test_missing_file_returns_empty_state(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "nonexistent" / "cb.json")
        assert cb.is_open("wrench") is False

    def test_custom_threshold(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json", threshold=5)
        for _ in range(4):
            cb.record_timeout("wrench")
        assert cb.is_open("wrench") is False
        cb.record_timeout("wrench")
        assert cb.is_open("wrench") is True

    def test_different_agents_tracked_separately(self, tmp_path: Path) -> None:
        cb = CircuitBreaker(tmp_path / "cb.json", threshold=2)
        cb.record_timeout("wrench")
        cb.record_timeout("wrench")
        assert cb.is_open("wrench") is True
        assert cb.is_open("scope") is False

    def test_state_file_is_json(self, tmp_path: Path) -> None:
        path = tmp_path / "cb.json"
        cb = CircuitBreaker(path)
        cb.record_timeout("wrench")
        data = json.loads(path.read_text())
        assert "wrench" in data
        assert data["wrench"]["consecutive_timeouts"] == 1
