"""
pipedbg.display
===============
All Rich terminal UI: live pipeline tree, step log streaming,
job summary table, and failure highlighting.
"""

from __future__ import annotations

import time
from typing import Generator

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .parsers.base import Job, Step, Workflow
from .runner import JobResult, JobStatus, StepResult, StepStatus

console = Console()

STATUS_ICONS = {
    StepStatus.PENDING:    "[dim]○[/dim]",
    StepStatus.RUNNING:    "[yellow]●[/yellow]",
    StepStatus.SUCCESS:    "[green]✓[/green]",
    StepStatus.FAILED:     "[red]✗[/red]",
    StepStatus.SKIPPED:    "[dim]–[/dim]",
    StepStatus.BREAKPOINT: "[yellow]⏸[/yellow]",
}

JOB_ICONS = {
    JobStatus.PENDING: "[dim]○[/dim]",
    JobStatus.RUNNING: "[yellow]●[/yellow]",
    JobStatus.SUCCESS: "[green]✓[/green]",
    JobStatus.FAILED:  "[red]✗[/red]",
    JobStatus.SKIPPED: "[dim]–[/dim]",
}


def print_workflow_header(workflow: Workflow) -> None:
    levels = workflow.job_levels()
    tree = Tree(f"[bold]{workflow.name}[/bold]  [dim]{workflow.path.name}[/dim]")
    for i, level in enumerate(levels):
        level_node = tree.add(f"[dim]Level {i + 1}[/dim]")
        for job_id in level:
            job = workflow.jobs[job_id]
            job_node = level_node.add(
                f"[bold]{job.name}[/bold]  [dim]runs-on: {job.runs_on}[/dim]"
            )
            for step in job.steps:
                bp_marker = " [yellow bold]⏸[/yellow bold]" if step.breakpoint else ""
                label = step.display_name()
                job_node.add(f"[dim]{label}[/dim]{bp_marker}")
    console.print()
    console.print(tree)
    console.print()


def make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=24),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def stream_step_logs(result: StepResult, verbose: bool = False) -> None:
    """Print logs for a step, truncated unless verbose."""
    if not result.logs:
        return
    if verbose or result.status == StepStatus.FAILED:
        for line in result.logs:
            console.print(f"  [dim]│[/dim] {line}")
    else:
        # Show first 3 + last 3 lines
        lines = result.logs
        if len(lines) <= 6:
            for line in lines:
                console.print(f"  [dim]│[/dim] {line}")
        else:
            for line in lines[:3]:
                console.print(f"  [dim]│[/dim] {line}")
            console.print(f"  [dim]│  … {len(lines) - 6} lines hidden (use --verbose to show all)[/dim]")
            for line in lines[-3:]:
                console.print(f"  [dim]│[/dim] {line}")


def print_step_result(result: StepResult, verbose: bool = False) -> None:
    icon = STATUS_ICONS[result.status]
    name = result.step.display_name()
    duration = f"[dim]{result.duration_seconds:.1f}s[/dim]"
    bp_tag = " [yellow]BREAKPOINT[/yellow]" if result.step.breakpoint else ""
    console.print(f"  {icon} {name}{bp_tag}  {duration}")
    stream_step_logs(result, verbose)
    if result.status == StepStatus.FAILED:
        console.print(
            f"  [red]   Exit code {result.exit_code}[/red]"
        )


def print_job_result(result: JobResult, verbose: bool = False) -> None:
    icon = JOB_ICONS[result.status]
    duration = f"{result.duration_seconds:.1f}s"
    status_color = "green" if result.status == JobStatus.SUCCESS else "red"
    console.print()
    console.print(
        f"{icon} [bold]{result.job.name}[/bold]  "
        f"[{status_color}]{result.status.name}[/{status_color}]  "
        f"[dim]{duration}[/dim]"
    )
    for step_result in result.step_results:
        print_step_result(step_result, verbose)


def print_summary(job_results: list[JobResult], total_seconds: float) -> None:
    table = Table(
        title="Pipeline Summary",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Job", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Steps", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Failed step")

    all_ok = True
    for r in job_results:
        if r.status != JobStatus.SUCCESS:
            all_ok = False
        status_text = Text(r.status.name)
        status_text.stylize("green" if r.status == JobStatus.SUCCESS else "red")

        failed_name = ""
        if r.failed_step:
            failed_name = r.failed_step.step.display_name()

        steps_ok = sum(1 for s in r.step_results if s.status == StepStatus.SUCCESS)
        steps_total = len(r.step_results)

        table.add_row(
            r.job.name,
            status_text,
            f"{steps_ok}/{steps_total}",
            f"{r.duration_seconds:.1f}s",
            failed_name,
        )

    console.print()
    console.print(table)

    total_color = "green" if all_ok else "red"
    result_label = "PASSED" if all_ok else "FAILED"
    console.print(
        f"\n[{total_color} bold]{result_label}[/{total_color} bold]  "
        f"[dim]Total: {total_seconds:.1f}s[/dim]"
    )
    console.print()


def print_failure_panel(result: StepResult) -> None:
    """Print a highlighted failure box for a failed step."""
    lines = "\n".join(result.logs[-20:])  # last 20 lines
    panel = Panel(
        lines or "(no output)",
        title=f"[red bold]✗ Failed: {result.step.display_name()}[/red bold]",
        subtitle=f"[red]exit code {result.exit_code}[/red]",
        border_style="red",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)


def print_breakpoints_found(workflow: Workflow) -> None:
    """Print a summary of all breakpoints found in the workflow."""
    found = []
    for job in workflow.jobs.values():
        for step in job.steps:
            if step.breakpoint:
                found.append((job.name, step.display_name(), step.id))

    if not found:
        return

    console.print()
    console.print("[yellow bold]⏸  Breakpoints detected:[/yellow bold]")
    for job_name, step_name, step_id in found:
        console.print(f"   [dim]{job_name}[/dim] → {step_name} [dim](id: {step_id})[/dim]")
    console.print()