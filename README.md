# pipedbg

Debug CI/CD pipelines locally with breakpoints, live logs, and an optional web UI.

## Features

- GitHub Actions, GitLab CI, and CircleCI parsing (GitHub always free)
- Breakpoints using `# breakpoint` in any script line
- Local Docker execution with interactive shells
- Web UI with live DAG, logs, and session sharing
- Pro-tier gates for sharing, notifications, and multi-platform parsing

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Run the CLI:

```bash
pipedbg run .github/workflows/ci.yml
pipedbg run .github/workflows/ci.yml --break-on "Run tests"
pipedbg inspect .github/workflows/ci.yml
pipedbg validate .github/workflows/ci.yml
```

Run the web UI:

```bash
pipedbg ui .github/workflows/ci.yml
```

<<<<<<< HEAD
Open the printed URL in multiple browsers to collaborate on the same debug session.
=======
## Pro Features

- Unlimited AI explain calls
- Session sharing (`pipedbg share`)
- Notifications (`pipedbg run --notify <webhook>`) 
- Multi-platform parsing (GitLab + CircleCI)
- Audit logging

Upgrade: https://pipedbg.dev/pro

## Auth Commands

```bash
pipedbg auth login --key <LICENSE_KEY>
pipedbg auth status
pipedbg auth logout
```

## Web UI

The web UI runs on port 7337 by default and provides live DAG updates via WebSocket.

```bash
pipedbg ui .github/workflows/ci.yml
pipedbg share .github/workflows/ci.yml
```

## VS Code Extension

The VS Code extension lives in [vscode-extension/](vscode-extension/README.md).
It integrates the CLI to provide:

- Gutter breakpoints
- Pipeline TreeView
- Command Palette actions
- Status bar updates

## Examples

- GitHub Actions: [examples/.github/workflows](examples/.github/workflows)
- GitLab CI: [examples/gitlab/.gitlab-ci.yml](examples/gitlab/.gitlab-ci.yml)
- CircleCI: [examples/circleci/.circleci/config.yml](examples/circleci/.circleci/config.yml)
>>>>>>> eb074399 (feat: add multi-platform parsers, auth, web UI, VS Code extension)
