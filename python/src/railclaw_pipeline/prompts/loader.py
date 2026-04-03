"""Jinja2 template loader from factory/ directory with sandboxed rendering."""

import re
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, StrictUndefined, TemplateSyntaxError, select_autoescape
from jinja2.sandbox import SandboxedEnvironment


class SandboxedTemplateError(Exception):
    """Raised when template rendering fails or is unsafe."""
    pass


class FactoryTemplateLoader(BaseLoader):
    """Loads Jinja2 templates from the factory/ directory.

    Templates are loaded from factory/ relative paths.
    Only .j2 files are allowed.
    """

    def __init__(self, factory_path: Path, templates_dir: str = "prompts/templates") -> None:
        self.factory_path = factory_path
        self.templates_dir = factory_path / templates_dir
        # Also support loading from plugin's bundled templates
        self.bundled_dir = Path(__file__).parent / "templates"

    def get_source(self, environment: Any, template: str) -> tuple[str, str, callable]:
        """Get template source from factory or bundled templates.

        Template names are sanitized to prevent directory traversal.
        """
        # Sanitize template name — no path traversal
        if ".." in template or template.startswith("/"):
            raise TemplateSyntaxError(f"Invalid template name: {template}", 0)

        # Only allow .j2 extension
        if not template.endswith(".j2"):
            raise TemplateSyntaxError(f"Template must have .j2 extension: {template}", 0)

        # Remove any non-alphanumeric/path characters
        safe_name = re.sub(r"[^\w./-]", "", template)

        # Try factory templates first, then bundled
        for base in [self.templates_dir, self.bundled_dir]:
            path = base / safe_name
            if path.exists() and path.is_file():
                source = path.read_text(encoding="utf-8")
                # Check for obvious SSTI attempts
                if self._has_unsafe_patterns(source):
                    raise SandboxedTemplateError(f"Potentially unsafe template: {template}")
                return source, str(path), lambda: False  # not cached

        raise TemplateSyntaxError(f"Template not found: {template}", 0)

    def _has_unsafe_patterns(self, source: str) -> bool:
        """Check for obviously unsafe Jinja2 patterns.

        Only inspects content inside {{ }} and {% %} delimiters.
        Plain text containing words like "subprocess" is safe —
        it only becomes dangerous when used as a Jinja2 expression.
        """
        unsafe = [
            "__import__",
            "__class__",
            "__mro__",
            "__subclasses__",
            "__builtins__",
            "os.system",
            "subprocess",
            "eval(",
            "exec(",
            "open(",
            "getattr(",
            "setattr(",
        ]
        # Extract Jinja expressions and statements only
        jinja_blocks = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", source, re.DOTALL)
        for block in jinja_blocks:
            lower = block.lower()
            if any(u.lower() in lower for u in unsafe):
                return True
        return False


def create_template_env(factory_path: Path) -> SandboxedEnvironment:
    """Create a sandboxed Jinja2 environment for factory templates.

    Uses jinja2.sandbox.SandboxedEnvironment for proper SSTI protection
    instead of blacklist-based pattern filtering.

    - StrictUndefined: variables must be defined
    - autoescape: disabled (we output text, not HTML)
    - No access to Python internals
    """
    loader = FactoryTemplateLoader(factory_path)
    env = SandboxedEnvironment(
        loader=loader,
        undefined=StrictUndefined,
        autoescape=select_autoescape(default=False),
        keep_trailing_newline=True,
    )
    return env


def render_template(
    factory_path: Path,
    template_name: str,
    context: dict[str, Any],
) -> str:
    """Render a template with the given context.

    Args:
        factory_path: Path to factory directory.
        template_name: Name of the .j2 template file.
        context: Variables to pass to the template.

    Returns:
        Rendered template string.

    Raises:
        SandboxedTemplateError: If template is unsafe or not found.
    """
    env = create_template_env(factory_path)
    try:
        template = env.get_template(template_name)
        return template.render(**context)
    except TemplateSyntaxError as exc:
        raise SandboxedTemplateError(f"Template error: {exc}") from exc


def load_prompt_text(factory_path: Path, prompt_name: str) -> str:
    """Load a raw prompt template text without rendering.

    Falls back to built-in prompts if factory template doesn't exist.
    """
    safe_name = re.sub(r"[^\w./-]", "", prompt_name)
    for base in [
        factory_path / "prompts" / "templates",
        Path(__file__).parent / "templates",
    ]:
        path = base / f"{safe_name}.j2"
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise SandboxedTemplateError(f"Prompt template not found: {prompt_name}")
