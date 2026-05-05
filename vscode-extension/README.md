# pipedbg VS Code Extension

A lightweight VS Code extension that integrates the pipedbg CLI into your workflow.

## Features

- YAML gutter breakpoints for `run:` steps in GitHub Actions workflows
- Explorer sidebar view of workflows, jobs, and steps
- Command Palette commands to run, dry-run, inspect, and validate workflows
- Integrated terminal streaming with automatic focus on breakpoints
- Status bar updates for pipeline state

## Commands

- `pipedbg: Run Workflow`
- `pipedbg: Run Current Workflow`
- `pipedbg: Dry Run`
- `pipedbg: Inspect Workflow`
- `pipedbg: Validate All Workflows`

## Settings

- `pipedbg.pythonPath` (default: `python3`)
- `pipedbg.envFile` (default: `.env`)
- `pipedbg.autoOpenTerminal` (default: true)
- `pipedbg.dockerEnabled` (default: true)

## Notes

This extension shells out to the `pipedbg` CLI and does not reimplement pipeline logic.
