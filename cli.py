"""
pipedbg CLI
===========
Run, inspect, and debug CI/CD workflows locally.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console

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
from .auth import (
    LicenseError,
    ProFeatureError,
    UsageLimitError,
    check_ai_limit,
    get_license,
    is_pro,
    render_pro_message,
    save_license,
)
from .auth.limits import record_audit_log
from .auth.gate import require_pro
from .web.server import run_ui_server


def _infer_repo_path(workflow_file: Path) -> Path:
    parts = workflow_file.parts
    if ".github" in parts and "workflows" in parts:
        try:
            idx = parts.index(".github")
            return Path(*parts[:idx])
        except Exception:
            pass
    return workflow_file.parent


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> dict:
    url = f"http://{host}:{port}/api/session"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("UI server did not start in time.")


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


@click.group()
@click.version_option("0.2.0", prog_name="pipedbg")
def cli() -> None:
    """pipedbg — debug CI/CD pipelines locally."""


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--job", "jobs", multiple=True, help="Run only specific job(s).")
@click.option("--dry-run", is_flag=True, help="Parse and print commands without executing.")
@click.option("--break-on", "break_on", multiple=True, help="Force a breakpoint by name or ID.")
@click.option("--env-file", type=click.Path(path_type=Path), default=None, help="Path to a .env file.")
@click.option("--no-docker", is_flag=True, help="Run on host instead of Docker.")
@click.option("--verbose", is_flag=True, help="Show full step logs.")
@click.option("--apply-ai-fix", is_flag=True, help="Offer to apply AI YAML changes.")
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
@click.option("--ui", is_flag=True, help="Open the web UI and stream this run.")
@click.option("--notify", type=str, default=None, help="Webhook URL to notify on pass/fail (Pro).")
@click.option("--ui-host", default="127.0.0.1", show_default=True)
@click.option("--ui-port", default=7337, show_default=True, type=int)
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
    ui: bool,
    notify: str | None,
    ui_host: str,
    ui_port: int,
) -> None:
    """Run a workflow file locally."""
    repo_path = repo or _infer_repo_path(workflow_file)

    if ui:
        thread = threading.Thread(
            target=run_ui_server,
            kwargs={
                "workflow_path": workflow_file,
                "repo_path": repo_path,
                "host": ui_host,
                "port": ui_port,
                "open_browser": True,
                "share": False,
            },
            daemon=True,
        )
        thread.start()
        session = _wait_for_server(ui_host, ui_port)
        _post_json(f"http://{ui_host}:{ui_port}/api/run", {
            "session_id": session.get("session_id"),
            "workflow": str(workflow_file),
            "dry_run": dry_run,
            "env_file": str(env_file) if env_file else None,
        })
        console.print("[green]UI server running. Press Ctrl+C to stop.[/green]")
        try:
            thread.join()
        except KeyboardInterrupt:
            console.print("\n[dim]UI server stopped.[/dim]")
        return

    try:
        workflow = parse_pipeline(workflow_file)
    except (WorkflowParseError, FileNotFoundError, ProFeatureError) as e:
        console.print(f"[red bold]Parse error:[/red bold] {e}")
        sys.exit(1)

    env_file = env_file or (repo_path / ".env")
    secrets = load_secrets(env_file)
    if secrets:
        console.print(f"[dim]Loaded {len(secrets)} secret(s) from {env_file}[/dim]")

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

    run_order = [j for j in execution_order if j in selected_jobs]

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
                try:
                    check_ai_limit()
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
                except UsageLimitError:
                    console.print(render_pro_message("AI explanations"))
                except Exception as e:
                    console.print(f"[dim]AI explain failed: {e}[/dim]")

    print_summary(all_results, time.time() - total_start)

    if notify and not is_pro():
        raise ProFeatureError("notifications")
    if notify:
        status = "failed" if pipeline_failed else "passed"
        _post_json(notify, {"status": status, "workflow": workflow.name})

    if is_pro():
        record_audit_log({
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "workflow": workflow.name,
            "status": "failed" if pipeline_failed else "passed",
        })

    if pipeline_failed:
        console.print(
            "[dim]Tip: run with [bold]--verbose[/bold] to see full logs, "
            "or add [bold]# breakpoint[/bold] to a step to pause before it runs.[/dim]"
        )
        sys.exit(1)


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output JSON for tooling.")
def inspect(workflow_file: Path, as_json: bool) -> None:
    """Parse and display the structure of a workflow file."""
    try:
        workflow = parse_pipeline(workflow_file)
    except (WorkflowParseError, FileNotFoundError, ProFeatureError) as e:
        console.print(f"[red bold]Parse error:[/red bold] {e}")
        sys.exit(1)

    if as_json:
        payload = {
            "name": workflow.name,
            "platform": workflow.platform,
            "jobs": {
                job_id: {
                    "id": job.id,
                    "name": job.name,
                    "needs": job.needs,
                    "steps": [
                        {
                            "id": step.id,
                            "name": step.display_name(),
                            "run": step.run,
                            "uses": step.uses,
                            "breakpoint": step.breakpoint,
                        }
                        for step in job.steps
                    ],
                }
                for job_id, job in workflow.jobs.items()
            },
        }
        click.echo(json.dumps(payload))
        return

    console.print()
    console.print(f"[bold]{workflow.name}[/bold]  [dim]{workflow_file}[/dim]")

    if workflow.on_triggers:
        triggers = ", ".join(str(k) for k in workflow.on_triggers.keys())
        console.print(f"[dim]Triggers:[/dim] {triggers}")

    if workflow.env:
        console.print(f"[dim]Workflow env vars:[/dim] {', '.join(workflow.env.keys())}")

    print_workflow_header(workflow)
    print_breakpoints_found(workflow)

    try:
        order = workflow.execution_order()
        levels = workflow.job_levels()
        console.print(f"[dim]Execution order:[/dim] {' → '.join(order)}")
        console.print(f"[dim]Parallel levels:[/dim] {len(levels)}")
    except WorkflowParseError as e:
        console.print(f"[red]Dependency error: {e}[/red]")

    total_steps = sum(len(j.steps) for j in workflow.jobs.values())
    bp_count = sum(
        1 for j in workflow.jobs.values() for s in j.steps if s.breakpoint
    )
    console.print(
        f"\n[dim]Jobs: {len(workflow.jobs)}  "
        f"Steps: {total_steps}  "
        f"Breakpoints: {bp_count}[/dim]"
    )
    console.print()


@cli.command(name="list")
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
def list_workflows(repo: Path | None) -> None:
    repo_path = repo or Path.cwd()
    paths = find_workflows(repo_path)

    if not paths:
        console.print(f"[dim]No workflow files found in {repo_path}[/dim]")
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


@cli.command()
@click.argument("workflow_file", type=click.Path(path_type=Path))
def validate(workflow_file: Path) -> None:
    if not workflow_file.exists():
        console.print(f"[red bold]File not found:[/red bold] {workflow_file}")
        sys.exit(1)

    try:
        workflow = parse_pipeline(workflow_file)
        _ = workflow.execution_order()
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


@cli.command(name="demo-ai")
@click.option("--apply-fix", is_flag=True, help="Apply the demo AI change to the temp file.")
def demo_ai(apply_fix: bool) -> None:
    from .parsers.base import Step
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
                    return type("Resp", (), {"content": [type("Item", (), {"text": payload})()]})()

        original_anthropic = ai.anthropic
        ai.anthropic = type("Anth", (), {"Anthropic": lambda api_key: FakeAnthropicClient()})()
        try:
            response = ai.explain_failure(demo_result)
        finally:
            ai.anthropic = original_anthropic

        console.print()
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


@cli.command(name="ui")
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=7337, show_default=True, type=int)
@click.option("--no-open", is_flag=True, help="Do not open browser automatically.")
def ui(workflow_file: Path, repo: Path | None, host: str, port: int, no_open: bool) -> None:
    repo_path = repo or _infer_repo_path(workflow_file)
    try:
        run_ui_server(
            workflow_path=workflow_file,
            repo_path=repo_path,
            host=host,
            port=port,
            open_browser=not no_open,
            share=False,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]UI server stopped.[/dim]")
    except ProFeatureError as e:
        console.print(render_pro_message(e.feature_name))
        sys.exit(1)
    except Exception as e:
        console.print(f"[red bold]UI error:[/red bold] {e}")
        sys.exit(1)


@cli.command(name="share")
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=7337, show_default=True, type=int)
def share(workflow_file: Path, repo: Path | None, host: str, port: int) -> None:
    repo_path = repo or _infer_repo_path(workflow_file)
    try:
        run_ui_server(
            workflow_path=workflow_file,
            repo_path=repo_path,
            host=host,
            port=port,
            open_browser=True,
            share=True,
        )
    except ProFeatureError as e:
        console.print(render_pro_message(e.feature_name))
        sys.exit(1)


@cli.group(name="auth")
def auth_group() -> None:
    """License management."""


@auth_group.command(name="login")
@click.option("--key", "license_key", required=True, help="License key (JWT)")
def auth_login(license_key: str) -> None:
    try:
        lic = save_license(license_key)
        console.print(f"[green]License saved.[/green] Tier: {lic.tier}, Expires: {lic.expires_at}")
    except LicenseError as e:
        console.print(f"[red]Invalid license: {e}[/red]")
        sys.exit(1)


@auth_group.command(name="status")
def auth_status() -> None:
    lic = get_license()
    if not lic:
        console.print("[yellow]No license found. Free tier active.[/yellow]")
        return
    console.print(f"[green]Active license:[/green] {lic.email}")
    console.print(f"Tier: {lic.tier}")
    console.print(f"Expires: {lic.expires_at}")


@auth_group.command(name="logout")
def auth_logout() -> None:
    from .auth.license import LICENSE_PATH
    if LICENSE_PATH.exists():
        LICENSE_PATH.unlink()
        console.print("[green]License removed.[/green]")
    else:
        console.print("[dim]No license file to remove.[/dim]")


def main() -> None:
    try:
        cli()
    except ProFeatureError as e:
        console.print(render_pro_message(e.feature_name))
        sys.exit(1)
    except UsageLimitError:
        console.print(render_pro_message("AI explanations"))
        sys.exit(1)


if __name__ == "__main__":
    main()
