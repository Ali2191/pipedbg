# pipedbg

![pipedbg demo](https://raw.githubusercontent.com/Ali2191/pipedbg/main/demo.gif)

Debug CI/CD pipelines locally with breakpoints, live logs, and an optional web UI.

## Features

- GitHub Actions, GitLab CI, and CircleCI parsing
- Breakpoints using `# breakpoint` in any script line
- Local Docker execution with interactive shells
- Web UI with live DAG, logs, and session sharing
- AI failure explanations powered by Claude

## Quick Start

```bash
pip install pipedbg
```

## Usage

```bash
pipedbg run .github/workflows/ci.yml
pipedbg run .github/workflows/ci.yml --break-on "Run tests"
pipedbg inspect .github/workflows/ci.yml
pipedbg validate .github/workflows/ci.yml
pipedbg ui .github/workflows/ci.yml
```

## Web UI

Runs on port 7337 with live DAG updates via WebSocket.

```bash
pipedbg ui .github/workflows/ci.yml
pipedbg share .github/workflows/ci.yml
```

## Pro Features

- Unlimited AI explain calls
- Session sharing (`pipedbg share`)
- Notifications (`pipedbg run --notify <webhook>`)
- Multi-platform parsing (GitLab + CircleCI)
- Audit logging

Upgrade: https://pipedbg.dev/pro

## Auth

```bash
pipedbg auth login --key <LICENSE_KEY>
pipedbg auth status
pipedbg auth logout
```

## VS Code Extension

Located in [vscode-extension/](vscode-extension/). Provides gutter breakpoints,
pipeline TreeView, Command Palette actions, and status bar updates.

## Examples

- GitHub Actions: [examples/.github/workflows](examples/.github/workflows)
- GitLab CI: [examples/gitlab/.gitlab-ci.yml](examples/gitlab/.gitlab-ci.yml)
- CircleCI: [examples/circleci/.circleci/config.yml](examples/circleci/.circleci/config.yml)

## License

MIT