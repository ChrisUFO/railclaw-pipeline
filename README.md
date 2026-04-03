# RailClaw Pipeline

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![OpenClaw Plugin](https://img.shields.io/badge/openclaw-plugin-purple.svg)](https://openclaw.ai)

OpenClaw plugin for automated coding factory pipeline orchestration. Plans, implements, reviews, merges, deploys, and QA-tests code changes from GitHub issues.

## Install

```bash
openclaw plugins install chrisufo/railclaw-pipeline
```

The postinstall script runs `pip install -e ./python` automatically.

## Quick Start

Add to your `openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "railclaw-pipeline": {
        "enabled": true,
        "config": {
          "repoPath": "/path/to/target/repo",
          "factoryPath": "/path/to/factory"
        }
      }
    }
  }
}
```

RailRunner (or any OpenClaw agent) can then invoke:

```
pipeline_run(action: "run", issueNumber: 42)
```

The pipeline runs as a **detached background process** — RailRunner stays responsive during long runs and receives stage handoff notifications as the pipeline progresses.

## Tool Actions

| Action          | Description                                                          |
| --------------- | -------------------------------------------------------------------- |
| `run`           | Start a new pipeline for an issue or milestone (detached by default) |
| `status`        | Check current pipeline state                                         |
| `resume`        | Resume an interrupted pipeline                                       |
| `abort`         | Cancel the active pipeline (kills background process)                |
| `notifications` | Get pending stage handoff notifications                              |

### Parameters

| Param         | Type                                                                  | Required            | Description                                             |
| ------------- | --------------------------------------------------------------------- | ------------------- | ------------------------------------------------------- |
| `action`      | `"run"` \| `"status"` \| `"resume"` \| `"abort"` \| `"notifications"` | Yes                 | Action to perform                                       |
| `issueNumber` | number                                                                | For `run`           | GitHub issue number                                     |
| `milestone`   | string                                                                | For `run`           | Milestone label for multi-issue mode                    |
| `hotfix`      | boolean                                                               | No                  | Run in hotfix mode (post-hoc review)                    |
| `forceStage`  | string                                                                | No                  | Force start at a specific stage                         |
| `detach`      | boolean                                                               | No                  | Run as background process (default: `true` from plugin) |
| `since`       | string                                                                | For `notifications` | ISO 8601 timestamp to filter notifications              |

## Pipeline Stages

| Stage           | Agent            | Description                          | Timeout |
| --------------- | ---------------- | ------------------------------------ | ------- |
| 0 — Preflight   | —                | Environment checks                   | 2m      |
| 1 — Blueprint   | Blueprint        | Planning                             | 10m     |
| 2 — Wrench      | Wrench           | Implementation                       | 2h      |
| 2.5 — PR        | —                | PR creation                          | 1m      |
| 3 — Audit       | Scope            | Completeness audit                   | 5m      |
| 3.5 — Audit Fix | Wrench           | Fix audit findings                   | 10m     |
| 4 — Review      | Scope            | Code review                          | 5m      |
| 5 — Fix Loop    | Wrench/Wrench Sr | Fix review findings (max 5 rounds)   | 10m     |
| Cycle 2         | Gemini           | External review loop (max 20 rounds) | 20m     |
| 7 — Docs        | Quill            | Documentation (opt-in)               | 10m     |
| 8 — Approval    | —                | Human approval gate                  | 24h     |
| 8c — Merge      | —                | Squash merge + branch delete         | 2m      |
| 9 — Deploy      | —                | PM2 restart + health check           | 5m      |
| 10 — QA         | Beaker           | QA sweep                             | 10m     |
| 11 — Hotfix     | Scope/Wrench     | Post-hoc hotfix review               | 30m     |
| 12 — Lessons    | —                | Lessons learned generation           | 2m      |

## Configuration

### Plugin Config (`openclaw.json`)

| Key                             | Default             | Description                              |
| ------------------------------- | ------------------- | ---------------------------------------- |
| `repoPath`                      | `.`                 | Target repository path                   |
| `factoryPath`                   | `factory`           | Factory directory with prompts and state |
| `pythonCommand`                 | `railclaw-pipeline` | Python CLI command or venv path          |
| `stateDir`                      | `.pipeline-state`   | State file directory                     |
| `eventsDir`                     | `.pipeline-events`  | Event log directory                      |
| `agents.*`                      | —                   | Per-agent model and timeout              |
| `timing.geminiPollInterval`     | `60`                | Seconds between Gemini review polls      |
| `timing.approvalTimeout`        | `86400`             | Max seconds to wait for human approval   |
| `escalation.wrenchSrAfterRound` | `3`                 | Switch to Wrench Sr after N fix rounds   |
| `escalation.chrisAfterRound`    | `5`                 | Escalate to Chris after N fix rounds     |

### Per-Repo Config (`.pipeline.json`)

Place a `.pipeline.json` at the repo root to override plugin defaults:

```json
{
  "factoryPath": "../factory",
  "agents": {
    "blueprint": { "model": "openai/gpt-5.4", "timeout": 600 }
  }
}
```

### Hotfix Mode

Set `hotfix: true` to run post-hoc review on a direct-to-main hotfix. Bypasses stages 0-2.5, runs Scope review on the diff, creates a follow-up PR if findings exist.

## Architecture

See [architecture.md](architecture.md) for the full system design.

```
railclaw-pipeline/
  src/                          TypeScript plugin layer
    index.ts                    Plugin registration
    tool.ts                     pipeline_run tool + TypeBox schema
    config.ts                   Config normalization
    python-bridge.ts            Subprocess wrapper + PID tracking
    store.ts                    Runtime store for active runs
    hooks.ts                    Gateway lifecycle hooks
  python/
    src/railclaw_pipeline/
      cli.py                    Click CLI
      pipeline.py               Stage runner orchestrator
      state/                    Pydantic models + atomic persistence
      runner/                   Agent subprocess execution
      stages/                   Pipeline stage implementations
      events/                   Event + notification emitters
      github/                   Git/gh CLI wrappers
      prompts/                  Jinja2 template loader (sandboxed)
      milestone/                Multi-issue milestone mode
```

## Development

```bash
npm install                    # Install JS deps + Python package
npm run build                  # TypeScript compile
npm run test                   # JS tests (Vitest)
npm run test:py                # Python tests (pytest)
npm run lint                   # ESLint
npm run lint:py                # Ruff
npm run typecheck              # tsc --noEmit
```

### Python Dev

```bash
cd python
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
```

## Security

- `shell=False` on all subprocess calls
- Jinja2 sandboxed templates (SSTI protection)
- Branch name sanitization before use in commands/paths
- No secrets in state files or event logs
- Atomic file writes for crash recovery

## License

MIT
