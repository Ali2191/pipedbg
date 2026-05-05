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

TEST_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCY3FwaJ+umfXbD
CBTWtr2Svu2Z6oFwMRndcz3l8yX9NdfdRQFS1tcGnpcNHGaZo8K/D0s6e9uTxAEF
Gldaf8ttpNJ82oV4WhH8UxKel6x+eLBkPcEkffSuZb2BgyDgC2FVxDS9dTKwhcWN
GqxZtBJatgP0fjt4BLz/M4TcQLmMreh1tsPbRT2tarADlTI4g82dGvcdQQFh2dGb
PLvG/KYbI53MGfEfPhYC+nqIFETde9UwlAer56lep4zA8F4T+xxeD11HM82l6uwY
7WPUaiSt1lwwjOe+D4Z11LXGDV7dU4GV0kykQNAtTsQSHjlrw3z4GtjjYVxqtgsD
kkoWg7q3AgMBAAECggEAOjzGKD7qVFF7lD15dv5DRmvQaTIDY4OJd6nGvNuArzI6
zjXSlcV9QavdH6Ug38sY0KLahesXUno76z5IZpXGorzHZsL4U8x5Crl5oAtoL/z6
Mw6mDamhNWpUo0mallEvobXxY/cJO2CTzbkKTdjBn2a2JgmLzaN8f/wYU7OjHZmy
7gl/zhZJVEX45FOCxAcCDZZHMhKYpEVIxoBdM8N00ya/4W8tUi2nF52dYjaE1Gv7
bRKk/YaDWFU+UN/YvbCK82HGkTPGFR5Wz21dQsM2NXQ1k9GmQxqvhwrn1GvnweIk
MrfydlkKA+21bIX8B7bdXxqEHIMup0H6IZVeD8+gkQKBgQDXXkRvr/Jqmip0SkIW
2q5lTGqaML9bQFsCV834Gxa4EK0gJRrKZgqEbsRK8zYfr4vBtS8bOauXJZAabZ09
cQD8LF1lGBG+vFvSeLwdOgoPFtq8PHplNvOlnf5D6gMJhbPWhgbbXIRhu5qYqfTb
r98Nb6nwygFaYU3ipKxRhwmSMQKBgQC1sySc//0XKLpJl9MAOLcaqeqrHohWN0C3
rHOvBAKXSjG5Y5qJaMXO8z5skVDtHKne7MvBLqr6dATy5Ahh7UWchuO2R1DxIkN4
XXhGe9uYrpDCIZfKt0mzzglcsGQq6FmNg6QUZrCKGLtWfg1xGDSfdJew9Bj4Cm+a
Oo6O5a05ZwKBgHfMeRcDcT45KVpsoBykYhP5EOdaLGdvAfDotKrJLrcOl67k1OU3
I6yNDOWAKmAvvvbueRiU2M0H2QPKa4fs3xZm+0CrxdsqXY1TGZjMWyIPnXbN0WuR
yLAclX5jonLei63N+ex1pzHSMGmxSIIXb2TC8238gAotTCzBWxUyn3FRAoGAGxCe
KYywBF0aso+c7HGGRMB+phKcOEtupm1XpgAw6pwwn+7IPCORI2x0JfPXXBpi60PW
beYnrbrOaeexn/SZ4+Dr1mD1G5YA+tLhcY5NfYazJVefpqB6p//OwTG9Ge8WN9Ae
BrPtJATfEtkf43K5k+7oEYGqnnffe9exGHP5w40CgYBhOZ7kEUlglAuVVkiZ2gNW
EoPo2L8pQ4C1betRAOC+beeB7yMAqDQ3kLW581Hs1yqiGJig0S1a+KZNvo5JWZuS
RkDfg1VVmb0DtyYGvf6lzFccWo+lnJaWpxL0F5bwsSC9nc3VgVtTI1JkqJLQRZTw
SYwRL0x7tLH5tBAG5anibg==
-----END PRIVATE KEY-----"""


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
    def test_circleci_parse_basic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPEDBG_LICENSE_KEY", _make_pro_token())
        path = tmp_path / "config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                """
                version: 2.1
                jobs:
                  build:
                    docker:
                      - image: cimg/python:3.11
                    steps:
                      - checkout
                      - run:
                          name: Test
                          command: pytest # breakpoint
                workflows:
                  build_flow:
                    jobs:
                      - build
                """
            )
        )
        wf = parse_any(path)
        assert wf.platform == "circleci"
        assert "build" in wf.jobs
        assert wf.jobs["build"].steps[1].breakpoint is True


class TestRunnerHelpers:
    def test_load_secrets_basic(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DB_URL=postgres://localhost\nAPI_KEY=secret123\n")
        secrets = load_secrets(env_file)
        assert secrets["DB_URL"] == "postgres://localhost"
        assert secrets["API_KEY"] == "secret123"

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

    def test_execute_step_dry_run(self, tmp_path):
        step = type("S", (), {"id": "s1", "name": "Run", "run": "echo hi", "uses": None, "with_inputs": {}, "env": {}, "breakpoint": False, "working_directory": None, "display_name": lambda self: "Run"})()
        result = execute_step(step, {}, tmp_path, None, dry_run=True)
        assert result.status == StepStatus.SUCCESS
