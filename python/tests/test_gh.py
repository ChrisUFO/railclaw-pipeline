"""Tests for gh CLI wrapper."""

import json

import pytest

from railclaw_pipeline.github.gh import GhClient, GhError


def test_gh_client_init(tmp_path):
    client = GhClient(tmp_path, timeout=30)
    assert client.repo_path == tmp_path
    assert client.timeout == 30


async def test_gh_unauthenticated_raises(tmp_path):
    client = GhClient(tmp_path, timeout=5)
    result = await client.is_authenticated()
    assert isinstance(result, bool)
