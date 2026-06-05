"""
Tests for pipedbg parser and runner.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import jwt
import pytest

from pipedbg.parsers import parse_workflow
from pipedbg.parsers.detect import parse_any
from pipedbg.parser import WorkflowParseError
from pipedbg.runner import (
    JobStatus,
    StepResult,
    StepStatus,
    execute_step,
    load_secrets,
    merge_env,
    resolve_image,
)

TEST_PRIVATE_KEY = os.environ.get('TEST_PRIVATE_KEY')

def _make_pro_token() -> str:
    payload = {
        "sub": "tester@example.com",
        "tier": "pro",
        "features": ["multi_platform"],
        "iat": 1714867200,
        "exp": 1893456000,
    }
    return jwt.encode(payload, TEST_PRIVATE_KEY, algorithm="RS256")


@pytest.fixture
def tmp_workflow(tmp_path: Path):
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


class TestGitLabParser:
    def test_gitlab_parse_basic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPEDBG_LICENSE_KEY", _make_pro_token())
        path = tmp_path / ".gitlab-ci.yml"
        path.write_text(
            textwrap.dedent(
                """
                stages: [build, test]
                variables:
                  GLOBAL: yes
                build:
                  stage: build
                  script:
                    - echo build
                test:
                  stage: test
                  needs: [build]
                  script:
                    - echo test # breakpoint
                """
            )
        )
        wf = parse_any(path)
        assert wf.platform == "gitlab"
        assert "build" in wf.jobs
        assert wf.jobs["test"].needs == ["build"]
        assert wf.jobs["test"].steps[0].breakpoint is True


class TestCircleCIParser:
    def test_circleci_parse_basic(self, tmp_path):
        path = tmp_path / ".circleci" / "config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                """
                version: 2.1
                jobs:
                  build:
                    docker:
                      - image: cimg/node:14.17.0
                    steps:
                      - run: echo build
                """
            )
        )
        wf = parse_any(path)
        assert wf.platform == "circleci"
        assert "build" in wf.jobs
        assert wf.jobs["build"].steps[0].run == "echo build"