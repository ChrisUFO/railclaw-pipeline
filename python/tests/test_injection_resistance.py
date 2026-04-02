"""Tests for injection resistance — SSTI and command injection."""

import pytest

from railclaw_pipeline.github.git import sanitize_branch_name
from railclaw_pipeline.prompts.loader import create_template_env, SandboxedTemplateError, render_template
from pathlib import Path


def test_sanitize_branch_name_normal():
    assert sanitize_branch_name("feat/issue-42") == "feat/issue-42"


def test_sanitize_branch_name_injection():
    malicious = "feat; rm -rf /"
    sanitized = sanitize_branch_name(malicious)
    assert ";" not in sanitized
    assert " " not in sanitized


def test_sanitize_branch_name_path_traversal():
    malicious = "../../../etc/passwd"
    sanitized = sanitize_branch_name(malicious)
    assert sanitized.count(".") == 0 or ".." not in sanitized


def test_sanitize_branch_name_backticks():
    malicious = "feat`whoami`"
    sanitized = sanitize_branch_name(malicious)
    assert "`" not in sanitized


def test_sanitize_branch_name_dollar():
    malicious = "feat$(whoami)"
    sanitized = sanitize_branch_name(malicious)
    assert "$" not in sanitized
    assert "(" not in sanitized


def test_template_ssti_import(tmp_path):
    env = create_template_env(tmp_path)
    template_dir = tmp_path / "prompts" / "templates"
    template_dir.mkdir(parents=True)

    malicious_template = template_dir / "evil.j2"
    malicious_template.write_text("{{ __import__('os').system('whoami') }}")

    with pytest.raises(SandboxedTemplateError, match="unsafe"):
        render_template(tmp_path, "evil.j2", {})


def test_template_ssti_builtins(tmp_path):
    env = create_template_env(tmp_path)
    template_dir = tmp_path / "prompts" / "templates"
    template_dir.mkdir(parents=True)

    malicious_template = template_dir / "evil2.j2"
    malicious_template.write_text("{{ ''.__class__.__mro__ }}")

    with pytest.raises(SandboxedTemplateError, match="unsafe"):
        render_template(tmp_path, "evil2.j2", {})


def test_template_path_traversal(tmp_path):
    with pytest.raises(Exception):
        render_template(tmp_path, "../../../etc/passwd", {})


def test_template_non_j2_extension(tmp_path):
    with pytest.raises(Exception):
        render_template(tmp_path, "setup.py", {})


def test_template_normal_render(tmp_path):
    template_dir = tmp_path / "prompts" / "templates"
    template_dir.mkdir(parents=True)
    template = template_dir / "test.j2"
    template.write_text("Hello {{ name }}, issue #{{ number }}!")

    result = render_template(tmp_path, "test.j2", {"name": "World", "number": 42})
    assert result == "Hello World, issue #42!"
