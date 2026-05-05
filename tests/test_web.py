"""
FastAPI endpoint tests.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from httpx import AsyncClient

from pipedbg.web import server
from pipedbg.parsers.github import parse_workflow


@pytest.mark.asyncio
async def test_session_endpoint(tmp_path: Path):
    workflow_path = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        textwrap.dedent(
            """
            name: Test
            on: [push]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hi
            """
        )
    )

    server.app.state.repo_path = str(tmp_path)
    server.app.state.workflow_path = str(workflow_path)
    session = server._sessions.get_or_create(workflow_path)
    session.reset(parse_workflow(workflow_path))
    server.app.state.default_session_id = session.session_id

    async with AsyncClient(app=server.app, base_url="http://test") as ac:
        resp = await ac.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow"]["name"] == "Test"


@pytest.mark.asyncio
async def test_license_endpoint(tmp_path: Path):
    server.app.state.repo_path = str(tmp_path)
    server.app.state.workflow_path = str(tmp_path / "ci.yml")
    async with AsyncClient(app=server.app, base_url="http://test") as ac:
        resp = await ac.get("/api/license")
        assert resp.status_code == 200
        assert "tier" in resp.json()
