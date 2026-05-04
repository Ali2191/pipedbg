# pipedbg

Debug CI/CD pipelines locally with breakpoints, local parity, and AI failure explanations.

Phase 3 now includes a local web UI with DAG visualization, step inspector,
breakpoint toggles, timeline, and session sharing via URL.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Run demo AI flow:

```bash
python -m pipedbg.cli demo-ai --apply-fix
```

Run the Phase 3 web UI:

```bash
python -m pipedbg.cli ui python-ci.yml
```

Open the printed URL in multiple browsers to collaborate on the same debug session.

Local plan gate:
- Free tier: limited AI explain calls in UI
- Team tier: unlimited AI explain calls (create a file named `.pipedbg-team` in repo root)
# pipedbg
