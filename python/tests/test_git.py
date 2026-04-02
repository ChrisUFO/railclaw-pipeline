"""Tests for git operations — sanitize and wrappers."""

from pathlib import Path

from railclaw_pipeline.github.git import sanitize_branch_name


def test_sanitize_normal():
    assert sanitize_branch_name("feat/issue-42-test") == "feat/issue-42-test"


def test_sanitize_spaces():
    result = sanitize_branch_name("feat/issue 42")
    assert " " not in result


def test_sanitize_semicolons():
    result = sanitize_branch_name("feat;echo")
    assert ";" not in result


def test_sanitize_pipes():
    result = sanitize_branch_name("feat|echo")
    assert "|" not in result


def test_sanitize_ampersand():
    result = sanitize_branch_name("feat&&echo")
    assert "&" not in result


def test_sanitize_newlines():
    result = sanitize_branch_name("feat\nmalicious")
    assert "\n" not in result


def test_sanitize_unicode():
    result = sanitize_branch_name("feat/\u0000test")
    assert "\u0000" not in result
