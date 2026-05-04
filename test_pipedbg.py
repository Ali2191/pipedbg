"""
Tests for pipedbg - parser, runner, and CLI.
Run with: pytest tests/ -v
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from pipedbg import ai
from pipedbg.cli import cli
from pipedbg.parser import (
  Job,
  Step,
  Workflow,
  WorkflowParseError,
  detect_platform,
  find_workflows,
  parse_pipeline,
  parse_workflow,
)
from pipedbg.runner import (
    JobStatus,
    StepResult,
    StepStatus,
    execute_step,
    load_secrets,
    merge_env,
    resolve_image,
)


@pytest.fixture
def tmp_workflow(tmp_path: Path):
    """Helper: write a YAML string to a temp workflow file and return its path."""

    def _write(content: str) -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        path = wf_dir / "test.yml"
        path.write_text(textwrap.dedent(content))
        return path

    return _write


class TestParserBasic:
    def test_minimal_workflow(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: My Pipeline
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - name: Echo
                    run: echo hello
        """
        )
        wf = parse_workflow(path)
        assert wf.name == "My Pipeline"
        assert "build" in wf.jobs
        assert len(wf.jobs["build"].steps) == 1
        assert wf.jobs["build"].steps[0].run == "echo hello"

    def test_workflow_env_parsed(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Env Test
            on: [push]
            env:
              NODE_ENV: test
              APP_NAME: myapp
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - run: echo $NODE_ENV
        """
        )
        wf = parse_workflow(path)
        assert wf.env["NODE_ENV"] == "test"
        assert wf.env["APP_NAME"] == "myapp"

    def test_step_uses_parsed(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Actions Test
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - name: Checkout
                    uses: actions/checkout@v4
                  - name: Setup Node
                    uses: actions/setup-node@v4
                    with:
                      node-version: "20"
        """
        )
        wf = parse_workflow(path)
        steps = wf.jobs["build"].steps
        assert steps[0].uses == "actions/checkout@v4"
        assert steps[1].uses == "actions/setup-node@v4"
        assert steps[1].with_inputs["node-version"] == "20"

    def test_job_needs_parsed(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Multi-job
            on: [push]
            jobs:
              lint:
                runs-on: ubuntu-latest
                steps:
                  - run: echo lint
              test:
                runs-on: ubuntu-latest
                needs: [lint]
                steps:
                  - run: echo test
        """
        )
        wf = parse_workflow(path)
        assert wf.jobs["test"].needs == ["lint"]

    def test_needs_string_form(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: String needs
            on: [push]
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo a
              b:
                runs-on: ubuntu-latest
                needs: a
                steps:
                  - run: echo b
        """
        )
        wf = parse_workflow(path)
        assert wf.jobs["b"].needs == ["a"]

    def test_step_env_parsed(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Step env
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - name: With env
                    run: echo $MY_VAR
                    env:
                      MY_VAR: hello
        """
        )
        wf = parse_workflow(path)
        assert wf.jobs["build"].steps[0].env["MY_VAR"] == "hello"


class TestBreakpointDetection:
    def test_breakpoint_comment_detected(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: BP Test
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Test step
                    run: |
                      # breakpoint
                      echo running tests
        """
        )
        wf = parse_workflow(path)
        step = wf.jobs["test"].steps[0]
        assert step.breakpoint is True

    def test_breakpoint_stripped_from_run_script(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Strip BP
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Step
                    run: |
                      # breakpoint
                      echo hello
                      echo world
        """
        )
        wf = parse_workflow(path)
        run_script = wf.jobs["test"].steps[0].run
        assert "breakpoint" not in run_script
        assert "echo hello" in run_script

    def test_breakpoint_case_insensitive(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Case BP
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                steps:
                  - name: Step
                    run: |
                      # BREAKPOINT
                      echo hi
        """
        )
        wf = parse_workflow(path)
        assert wf.jobs["test"].steps[0].breakpoint is True

    def test_no_breakpoint_by_default(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: No BP
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo no breakpoint here
        """
        )
        wf = parse_workflow(path)
        assert wf.jobs["build"].steps[0].breakpoint is False


class TestDependencyGraph:
    def test_execution_order_respects_needs(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Order test
            on: [push]
            jobs:
              c:
                runs-on: ubuntu-latest
                needs: [b]
                steps: [{run: echo c}]
              a:
                runs-on: ubuntu-latest
                steps: [{run: echo a}]
              b:
                runs-on: ubuntu-latest
                needs: [a]
                steps: [{run: echo b}]
        """
        )
        wf = parse_workflow(path)
        order = wf.execution_order()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_parallel_jobs_in_same_level(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Parallel
            on: [push]
            jobs:
              lint:
                runs-on: ubuntu-latest
                steps: [{run: echo lint}]
              test:
                runs-on: ubuntu-latest
                steps: [{run: echo test}]
              build:
                runs-on: ubuntu-latest
                needs: [lint, test]
                steps: [{run: echo build}]
        """
        )
        wf = parse_workflow(path)
        levels = wf.job_levels()
        assert set(levels[0]) == {"lint", "test"}
        assert levels[1] == ["build"]

    def test_unknown_needs_raises(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Bad needs
            on: [push]
            jobs:
              test:
                runs-on: ubuntu-latest
                needs: [nonexistent]
                steps: [{run: echo test}]
        """
        )
        with pytest.raises(WorkflowParseError, match="nonexistent"):
            parse_workflow(path)


class TestParserErrors:
    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_workflow(tmp_path / "nonexistent.yml")

    def test_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("key: [unclosed bracket")
        with pytest.raises(WorkflowParseError, match="YAML parse error"):
            parse_workflow(bad)

    def test_no_jobs_raises(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Empty
            on: [push]
        """
        )
        with pytest.raises(WorkflowParseError, match="no jobs"):
            parse_workflow(path)

    def test_malformed_job_raises(self, tmp_workflow):
        path = tmp_workflow(
            """
            name: Bad job
            on: [push]
            jobs:
              build: "this should be a mapping"
        """
        )
        with pytest.raises(WorkflowParseError, match="malformed"):
            parse_workflow(path)


class TestStepDisplayName:
    def test_named_step(self):
        s = Step(id="s1", name="Run tests", run="pytest")
        assert s.display_name() == "Run tests"

    def test_uses_step(self):
        s = Step(id="s1", name="", uses="actions/checkout@v4")
        assert "actions/checkout@v4" in s.display_name()

    def test_run_step_truncated(self):
        long_cmd = "echo " + "a" * 100
        s = Step(id="s1", name="", run=long_cmd)
        assert len(s.display_name()) <= 63


class TestRunnerHelpers:
    def test_load_secrets_basic(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DB_URL=postgres://localhost\nAPI_KEY=secret123\n")
        secrets = load_secrets(env_file)
        assert secrets["DB_URL"] == "postgres://localhost"
        assert secrets["API_KEY"] == "secret123"

    def test_load_secrets_ignores_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# this is a comment\nFOO=bar\n")
        secrets = load_secrets(env_file)
        assert "# this is a comment" not in secrets
        assert secrets["FOO"] == "bar"

    def test_load_secrets_missing_file(self, tmp_path):
        secrets = load_secrets(tmp_path / "nonexistent.env")
        assert secrets == {}

    def test_merge_env_precedence(self):
        result = merge_env(
            workflow_env={"A": "workflow", "B": "workflow"},
            job_env={"B": "job", "C": "job"},
            step_env={"C": "step", "D": "step"},
            secrets={"D": "secret", "E": "secret"},
        )
        assert result["A"] == "workflow"
        assert result["B"] == "job"
        assert result["C"] == "step"
        assert result["D"] == "secret"
        assert result["E"] == "secret"

    def test_resolve_image_known(self):
        assert resolve_image("ubuntu-latest") == "ubuntu:22.04"
        assert resolve_image("ubuntu-22.04") == "ubuntu:22.04"

    def test_resolve_image_fallback(self):
        assert resolve_image("some-custom-runner") == "ubuntu:22.04"


class TestAIFeatures:
    def test_explain_failure_parses_structured_json(self, monkeypatch):
        step = Step(id="step-1", name="Run tests", run="pytest")
        result = StepResult(step=step, status=StepStatus.FAILED, exit_code=1, logs=["line 1", "line 2"])

        class FakeAnthropicClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    payload = {
                        "explanation": "pytest is missing in the image.",
                        "suggestion": "Install pytest before running tests.",
                        "changes": [
                            {
                                "find": "run: pytest",
                                "replace": "run: pip install pytest && pytest",
                            }
                        ],
                    }
                    return SimpleNamespace(content=[SimpleNamespace(text=json_dumps(payload))])

        def json_dumps(payload):
            import json

            return json.dumps(payload)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(ai, "anthropic", SimpleNamespace(Anthropic=lambda api_key: FakeAnthropicClient()))

        parsed = ai.explain_failure(result)
        assert parsed["explanation"] == "pytest is missing in the image."
        assert parsed["suggestion"] == "Install pytest before running tests."
        assert parsed["changes"][0]["find"] == "run: pytest"

    def test_apply_yaml_changes_writes_backup_and_updates_file(self, tmp_path):
        workflow = tmp_path / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: CI\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n")

        backup = ai.apply_yaml_changes(
            workflow,
            [{"find": "- run: pytest", "replace": "- run: pip install pytest && pytest"}],
        )

        assert backup.exists()
        assert backup.read_text() == "name: CI\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
        assert "pip install pytest && pytest" in workflow.read_text()

    def test_demo_ai_command_runs_without_api_key(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["demo-ai"])
        assert result.exit_code == 0
        assert "AI demo" in result.output
        assert "Explanation:" in result.output
        assert "Suggested fix:" in result.output


class TestBreakpointExecution:
    def test_break_on_step_id_triggers_breakpoint_shell(self, monkeypatch, tmp_path):
        step = Step(id="step-1", name="Run tests", run="echo ok")
        job = Job(id="build", name="build", runs_on="ubuntu-latest", steps=[step])
        Workflow(name="ci", path=tmp_path / "ci.yml", on_triggers={}, env={}, jobs={"build": job})

        called = {}

        def fake_breakpoint_shell_local(step_arg, env_arg, cwd_arg):
            called["step_id"] = step_arg.id
            called["cwd"] = cwd_arg
            called["env_has_workspace"] = "GITHUB_WORKSPACE" in env_arg

        class FakeProc:
            def __init__(self, *args, **kwargs):
                self.stdout = iter(["ok"])
                self.returncode = 0

            def wait(self):
                return 0

        monkeypatch.setattr("pipedbg.runner.open_breakpoint_shell_local", fake_breakpoint_shell_local)
        monkeypatch.setattr("pipedbg.runner.subprocess.Popen", FakeProc)

        result = execute_step(
            step=step,
            env={"GITHUB_WORKSPACE": str(tmp_path)},
            repo_path=tmp_path,
            container=None,
            dry_run=False,
            force_breakpoints=["step-1"],
        )

        assert called["step_id"] == "step-1"
        assert called["env_has_workspace"] is True
        assert result.status == StepStatus.SUCCESS


class TestFindWorkflows:
    def test_finds_yml_and_yaml(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
        )
        (wf_dir / "deploy.yaml").write_text(
            "name: Deploy\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
        )
        (wf_dir / "readme.md").write_text("# not a workflow")

        found = find_workflows(tmp_path)
        names = [p.name for p in found]
        assert "ci.yml" in names
        assert "deploy.yaml" in names
        assert "readme.md" not in names

    def test_no_workflows_dir(self, tmp_path):
        assert find_workflows(tmp_path) == []


class TestMultiPlatformParser:
    def test_parse_gitlab_ci(self, tmp_path):
        path = tmp_path / ".gitlab-ci.yml"
        path.write_text(
            textwrap.dedent(
                """
                stages: [test, deploy]
                variables:
                  APP_ENV: ci

                test-job:
                  stage: test
                  script:
                    - echo testing

                deploy-job:
                  stage: deploy
                  script:
                    - echo deploy
                """
            )
        )

        wf = parse_pipeline(path)
        assert wf.platform == "gitlab"
        assert "test-job" in wf.jobs
        assert "deploy-job" in wf.jobs
        assert wf.jobs["deploy-job"].needs == ["test-job"]

    def test_parse_circleci_config(self, tmp_path):
        circle = tmp_path / ".circleci"
        circle.mkdir()
        path = circle / "config.yml"
        path.write_text(
            textwrap.dedent(
                """
                version: 2.1
                jobs:
                  build:
                    docker:
                      - image: cimg/python:3.12
                    steps:
                      - checkout
                      - run: echo hello
                  test:
                    docker:
                      - image: cimg/python:3.12
                    steps:
                      - run:
                          name: Run tests
                          command: pytest
                workflows:
                  ci:
                    jobs:
                      - build
                      - test:
                          requires: [build]
                """
            )
        )

        wf = parse_pipeline(path)
        assert wf.platform == "circleci"
        assert wf.jobs["test"].needs == ["build"]
        assert len(wf.jobs["build"].steps) == 2

    def test_detect_platform_defaults_to_github(self, tmp_path):
        path = tmp_path / "ci.yml"
        path.write_text("name: CI\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n")
        assert detect_platform(path) == "github"