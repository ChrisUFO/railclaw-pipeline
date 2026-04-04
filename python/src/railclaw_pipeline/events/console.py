"""Rich console output for pipeline progress."""


from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from railclaw_pipeline.state.models import PipelineStage, PipelineState


class ConsoleReporter:
    """Rich-based console output for pipeline progress."""

    def __init__(self) -> None:
        self.console = Console()
        self._progress: Progress | None = None

    def stage_start(self, stage: PipelineStage, issue: int) -> None:
        """Print stage start message."""
        self.console.print(f"\n[cyan]▶ Stage:[/cyan] {stage.value}")
        self.console.print(f"[dim]Issue #{issue}[/dim]")

    def stage_end(self, stage: PipelineStage, success: bool, duration: float) -> None:
        """Print stage end message."""
        status = "[green]✓[/green]" if success else "[red]✗[/red]"
        duration_str = f"{duration:.1f}s"
        self.console.print(f"{status} {stage.value} completed in {duration_str}")

    def agent_start(self, agent: str) -> None:
        """Print agent start message."""
        self.console.print(f"[yellow]→ Agent:[/yellow] {agent}")

    def agent_end(self, agent: str, success: bool, duration: float) -> None:
        """Print agent end message."""
        status = "[green]✓[/green]" if success else "[red]✗[/red]"
        duration_str = f"{duration:.1f}s"
        self.console.print(f"{status} {agent} finished in {duration_str}")

    def error(self, message: str) -> None:
        """Print error message."""
        self.console.print(f"[red]ERROR:[/red] {message}")

    def info(self, message: str) -> None:
        """Print info message."""
        self.console.print(f"[blue]INFO:[/blue] {message}")

    def print_state(self, state: PipelineState) -> None:
        """Print current pipeline state."""
        table = Table(title=f"Pipeline State - Issue #{state.issue_number}")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Stage", state.stage.value)
        table.add_row("Status", state.status.value)
        if state.pr_number:
            table.add_row("PR", f"#{state.pr_number}")
        if state.branch:
            table.add_row("Branch", state.branch)
        if state.timestamps:
            table.add_row("Started", state.timestamps.started.isoformat())
            table.add_row("Last Updated", state.timestamps.last_updated.isoformat())

        self.console.print(table)
