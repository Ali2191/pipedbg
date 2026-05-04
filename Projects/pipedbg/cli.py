"""
pipedbg — CI/CD local debugger
================================
Run, debug, and inspect your GitHub Actions pipelines locally.

Commands
--------
  run       Execute a workflow (or specific jobs) locally
  inspect   Parse and visualize a workflow file structure
  list      List all workflows found in the repo
  validate  Validate workflow YAML without running it

Usage
-----
  pipedbg run .github/workflows/ci.yml
  pipedbg run .github/workflows/ci.yml --job build --dry-run
  pipedbg run .github/workflows/ci.yml --break-on "Run tests"
  pipedbg inspect .github/workflows/ci.yml
  pipedbg list
    pipedbg validate .github/workflows/ci.yml
    pipedbg demo-ai
    pipedbg ui .github/workflows/ci.yml
"""

from __future__ import annotations

import sys
import time
import tempfile
from types import SimpleNamespace
from pathlib import Path

import click
from rich.console import Console
from rich.text import Text

from .display import (
    console,
    print_breakpoints_found,
    print_failure_panel,
    print_job_result,
    print_summary,
    print_workflow_header,
)
from .parser import WorkflowParseError, find_workflows, parse_pipeline
from .runner import JobStatus, StepStatus, load_secrets, run_job
from . import ai
from .webui import run_ui_server


def _infer_repo_path(workflow_file: Path) -> Path:
    """Infer a reasonable repository root from the workflow path.

    - If workflow is under `.github/workflows/`, return the repo root.
    - Otherwise, use the workflow file's parent directory.
    """
    parts = workflow_file.parts
    if ".github" in parts and "workflows" in parts:
        try:
            idx = parts.index(".github")
            return Path(*parts[:idx])
        except Exception:
            pass
    return workflow_file.parent

# ─────────────────────────────────────────
# CLI group
# ─────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="pipedbg")
def cli():
    """pipedbg — debug CI/CD pipelines locally."""


# ─────────────────────────────────────────
# run
# ─────────────────────────────────────────

@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--job", "-j", "jobs", multiple=True,
    help="Run only specific job(s). Can be specified multiple times.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Parse and print commands without executing them.",
)
@click.option(
    "--break-on", "-b", "break_on", multiple=True,
    help="Force a breakpoint on a step by name or ID. Can be specified multiple times.",
)
@click.option(
    "--env-file", "-e", type=click.Path(path_type=Path), default=None,
    help="Path to a .env file containing secrets (default: .env in current dir).",
)
@click.option(
    "--no-docker", is_flag=True,
    help="Run steps directly on the host instead of inside Docker containers.",
)
@click.option(
    "--verbose", "-v", is_flag=True,
    help="Show full step logs (default: truncated to 6 lines).",
)
@click.option(
    "--apply-ai-fix", is_flag=True,
    help="If AI suggests YAML changes, offer to apply them in-place with a backup copy.",
)
@click.option(
    "--repo", type=click.Path(path_type=Path), default=None,
    help="Path to the repository root (default: current directory).",
)
def run(
    workflow_file: Path,
    jobs: tuple[str, ...],
    dry_run: bool,
    break_on: tuple[str, ...],
    env_file: Path | None,
    no_docker: bool,
    verbose: bool,
    apply_ai_fix: bool,
    repo: Path | None,
):
    """Run a workflow file locally.

    \b
    Examples:
      pipedbg run .github/workflows/ci.yml
      pipedbg run ci.yml --job build --job test
      pipedbg run ci.yml --break-on "Run tests" --env-file .secrets
      pipedbg run ci.yml --dry-run
    """
    repo_path = repo or _infer_repo_path(workflow_file)

    # ── Parse ──
    try:
        workflow = parse_pipeline(workflow_file)
    except (WorkflowParseError, FileNotFoundError) as e:
        console.print(f"[red bold]Parse error:[/red bold] {e}")
        sys.exit(1)

    # ── Secrets ──
    env_file = env_file or (repo_path / ".env")
    secrets = load_secrets(env_file)
    if secrets:
        console.print(f"[dim]Loaded {len(secrets)} secret(s) from {env_file}[/dim]")

    # ── Header ──
    mode_tags = []
    if dry_run:
        mode_tags.append("[yellow]dry-run[/yellow]")
    if no_docker:
        mode_tags.append("[dim]no-docker[/dim]")
    if break_on:
        mode_tags.append(f"[yellow]break-on: {', '.join(break_on)}[/yellow]")

    console.print()
    console.print(
        f"[bold]pipedbg[/bold]  {workflow.name}"
        + (f"  {' '.join(mode_tags)}" if mode_tags else "")
    )
    print_workflow_header(workflow)
    print_breakpoints_found(workflow)

    # ── Job selection ──
    try:
        execution_order = workflow.execution_order()
    except WorkflowParseError as e:
        console.print(f"[red bold]Error:[/red bold] {e}")
        sys.exit(1)

    selected_jobs = list(jobs) if jobs else execution_order
    unknown = [j for j in selected_jobs if j not in workflow.jobs]
    if unknown:
        console.print(f"[red bold]Unknown job(s):[/red bold] {', '.join(unknown)}")
        console.print(f"Available: {', '.join(workflow.jobs.keys())}")
        sys.exit(1)

    # Filter to selected, preserving topological order
    run_order = [j for j in execution_order if j in selected_jobs]

    # ── Execute ──
    all_results = []
    total_start = time.time()
    pipeline_failed = False

    for job_id in run_order:
        job = workflow.jobs[job_id]

        if pipeline_failed:
            console.print(f"[dim]Skipping job `{job.name}` (upstream failure)[/dim]")
            continue

        console.rule(f"[bold]{job.name}[/bold]  [dim]{job.runs_on}[/dim]")

        result = run_job(
            job=job,
            workflow=workflow,
            repo_path=repo_path,
            secrets=secrets,
            dry_run=dry_run,
            force_breakpoints=list(break_on),
            use_docker=not no_docker,
        )
        all_results.append(result)
        print_job_result(result, verbose)

        if result.status == JobStatus.FAILED:
            pipeline_failed = True
            if result.failed_step:
                print_failure_panel(result.failed_step)
                # Phase 2: AI explain the failure and optionally apply a YAML fix
                try:
                    ai_resp = ai.explain_failure(result.failed_step)
                    console.print("\n[bold]AI explanation:[/bold]\n")
                    console.print(ai_resp.get("explanation", "(no explanation)"))
                    suggestion = ai_resp.get("suggestion", "")
                    changes = ai_resp.get("changes", []) or []
                    if suggestion:
                        console.print("\n[bold]AI suggested fix:[/bold]\n")
                        console.print(suggestion)
                    if changes:
                        console.print("\n[bold]AI YAML changes:[/bold]\n")
                        for idx, change in enumerate(changes, start=1):
                            console.print(f"  [dim]{idx}. find:[/dim] {change.get('find', '')}")
                            console.print(f"     [dim]replace:[/dim] {change.get('replace', '')}")
                        if apply_ai_fix and click.confirm("Apply these changes to the workflow file in-place?", default=False):
                            backup_path = ai.apply_yaml_changes(workflow_file, changes)
                            console.print(f"\n[green]Applied AI changes.[/green] Backup saved to [dim]{backup_path}[/dim]")
                        elif apply_ai_fix:
                            console.print("\n[dim]Skipped applying AI changes.[/dim]")
                        else:
                            console.print("\n[dim]Run with --apply-ai-fix to prompt for in-place application.[/dim]")
                except Exception as e:
                    console.print(f"[dim]AI explain failed: {e}[/dim]")

    # ── Summary ──
    print_summary(all_results, time.time() - total_start)

    if pipeline_failed:
        console.print(
            "[dim]Tip: run with [bold]--verbose[/bold] to see full logs, "
            "or add [bold]# breakpoint[/bold] to a step to pause before it runs.[/dim]"
        )
        sys.exit(1)


# ─────────────────────────────────────────
# inspect
# ─────────────────────────────────────────

@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
def inspect(workflow_file: Path):
    """Parse and display the structure of a workflow file.

    \b
    Example:
      pipedbg inspect .github/workflows/ci.yml
    """
    try:
        workflow = parse_pipeline(workflow_file)
    except (WorkflowParseError, FileNotFoundError) as e:
        console.print(f"[red bold]Parse error:[/red bold] {e}")
        sys.exit(1)

    console.print()
    console.print(f"[bold]{workflow.name}[/bold]  [dim]{workflow_file}[/dim]")

    if workflow.on_triggers:
        triggers = ", ".join(str(k) for k in workflow.on_triggers.keys())
        console.print(f"[dim]Triggers:[/dim] {triggers}")

    if workflow.env:
        console.print(f"[dim]Workflow env vars:[/dim] {', '.join(workflow.env.keys())}")

    print_workflow_header(workflow)
    print_breakpoints_found(workflow)

    # Execution order
    try:
        order = workflow.execution_order()
        levels = workflow.job_levels()
        console.print(f"[dim]Execution order:[/dim] {' → '.join(order)}")
        console.print(f"[dim]Parallel levels:[/dim] {len(levels)}")
    except WorkflowParseError as e:
        console.print(f"[red]Dependency error: {e}[/red]")

    # Stats
    total_steps = sum(len(j.steps) for j in workflow.jobs.values())
    bp_count = sum(
        1 for j in workflow.jobs.values()
        for s in j.steps if s.breakpoint
    )
    console.print(
        f"\n[dim]Jobs: {len(workflow.jobs)}  "
        f"Steps: {total_steps}  "
        f"Breakpoints: {bp_count}[/dim]"
    )
    console.print()


# ─────────────────────────────────────────
# list
# ─────────────────────────────────────────

@cli.command(name="list")
@click.option(
    "--repo", type=click.Path(path_type=Path), default=None,
    help="Repository root (default: current directory).",
)
def list_workflows(repo: Path | None):
    """List all workflow files found in .github/workflows/.

    \b
    Example:
      pipedbg list
      pipedbg list --repo /path/to/repo
    """
    repo_path = repo or Path.cwd()
    paths = find_workflows(repo_path)

    if not paths:
        console.print(f"[dim]No workflow files found in {repo_path / '.github' / 'workflows'}[/dim]")
        return

    console.print()
    for path in paths:
        try:
            wf = parse_pipeline(path)
            jobs_info = ", ".join(wf.jobs.keys())
            total_steps = sum(len(j.steps) for j in wf.jobs.values())
            console.print(
                f"[bold]{path.name}[/bold]  [dim]{wf.name}[/dim]\n"
                f"  Jobs: {jobs_info}  Steps: {total_steps}\n"
            )
        except WorkflowParseError as e:
            console.print(f"[red]{path.name}[/red]  [dim](parse error: {e})[/dim]\n")


# ─────────────────────────────────────────
# validate
# ─────────────────────────────────────────

@cli.command()
@click.argument("workflow_file", type=click.Path(path_type=Path))
def validate(workflow_file: Path):
    """Validate a workflow YAML file without running it.

    \b
    Checks:
      - Valid YAML syntax
      - Required fields (jobs, steps, runs-on)
      - Dependency graph (no circular needs:)
      - Unknown job references in needs:

    Example:
      pipedbg validate .github/workflows/ci.yml
    """
    if not workflow_file.exists():
        console.print(f"[red bold]File not found:[/red bold] {workflow_file}")
        sys.exit(1)

    try:
        workflow = parse_pipeline(workflow_file)
        _ = workflow.execution_order()  # triggers cycle detection
        console.print(f"[green bold]✓[/green bold] {workflow_file.name} is valid")
        console.print(
            f"  [dim]Jobs: {len(workflow.jobs)}  "
            f"Steps: {sum(len(j.steps) for j in workflow.jobs.values())}[/dim]"
        )
    except FileNotFoundError:
        console.print(f"[red bold]File not found:[/red bold] {workflow_file}")
        sys.exit(1)
    except WorkflowParseError as e:
        console.print(f"[red bold]✗[/red bold] Validation failed: {e}")
        sys.exit(1)


# ─────────────────────────────────────────
# demo-ai
# ─────────────────────────────────────────

@cli.command(name="demo-ai")
@click.option(
    "--apply-fix", is_flag=True,
    help="Apply the demo AI change to the temporary workflow file and show the backup path.",
)
def demo_ai(apply_fix: bool):
    """Run a fake Anthropic-backed AI explanation demo without an API key."""
    from .parser import Step
    from .runner import StepResult

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        workflow_file = tmp_path / "demo.yml"
        workflow_file.write_text(
            "name: Demo\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
            encoding="utf-8",
        )

        demo_step = Step(id="demo-step", name="Run tests", run="pytest")
        demo_result = StepResult(
            step=demo_step,
            status=StepStatus.FAILED,
            exit_code=1,
            logs=["pytest: command not found", "install missing dependency"],
        )

        class FakeAnthropicClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    payload = (
                        '{"explanation":"The runner image is missing pytest.",'
                        '"suggestion":"Install pytest before running tests.",'
                        '"changes":[{"find":"- run: pytest","replace":"- run: pip install pytest && pytest"}]}'
                    )
                    return SimpleNamespace(content=[SimpleNamespace(text=payload)])

        original_anthropic = ai.anthropic
        ai.anthropic = SimpleNamespace(Anthropic=lambda api_key: FakeAnthropicClient())
        try:
            response = ai.explain_failure(demo_result)
        finally:
            ai.anthropic = original_anthropic

        console.print()


# ─────────────────────────────────────────
# ui
# ─────────────────────────────────────────

@cli.command(name="ui")
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host for local UI server.")
@click.option("--port", default=8765, show_default=True, type=int, help="Port for local UI server.")
@click.option("--no-open", is_flag=True, help="Do not open a browser automatically.")
def ui(workflow_file: Path, repo: Path | None, host: str, port: int, no_open: bool):
    """Launch the local Phase 3 web UI for DAG + breakpoints + timeline."""
    repo_path = repo or _infer_repo_path(workflow_file)
    try:
        run_ui_server(
            workflow_file=workflow_file,
            repo_path=repo_path,
            host=host,
            port=port,
            open_browser=not no_open,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]UI server stopped.[/dim]")
    except Exception as e:
        console.print(f"[red bold]UI error:[/red bold] {e}")
        sys.exit(1)
        console.print("[bold]AI demo[/bold]")
        console.print(f"[dim]Workflow:[/dim] {workflow_file}")
        console.print(f"[dim]Step:[/dim] {demo_step.display_name()}  [dim](id: {demo_step.id})[/dim]")
        console.print()
        console.print("[bold]Explanation:[/bold]")
        console.print(response.get("explanation", "(no explanation)"))
        console.print()
        console.print("[bold]Suggested fix:[/bold]")
        console.print(response.get("suggestion", "(no suggestion)"))
        changes = response.get("changes", []) or []
        if changes:
            console.print()
            console.print("[bold]Proposed YAML changes:[/bold]")
            for change in changes:
                console.print(f"[dim]find:[/dim] {change.get('find', '')}")
                console.print(f"[dim]replace:[/dim] {change.get('replace', '')}")
            if apply_fix and click.confirm("Apply the demo change to the temporary workflow file?", default=True):
                backup_path = ai.apply_yaml_changes(workflow_file, changes)
                console.print(f"\n[green]Applied to demo file.[/green] Backup: [dim]{backup_path}[/dim]")
                console.print(workflow_file.read_text(encoding="utf-8"))
            elif apply_fix:
                console.print("\n[dim]Skipped applying demo change.[/dim]")
        console.print()


# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()