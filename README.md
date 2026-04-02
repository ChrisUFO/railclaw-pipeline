# railclaw-pipeline

OpenClaw plugin for automated coding factory pipeline orchestration. Plans, implements, reviews, merges, deploys, and QA-tests code changes from GitHub issues.

## Installation

```bash
openclaw plugins install chrisufo/railclaw-pipeline
```

The postinstall script runs `pip install -e ./python` automatically.

## Configuration

Add to your `openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "railclaw-pipeline": {
        "enabled": true,
        "config": {
          "repoPath": "/path/to/target/repo",
          "factoryPath": "/path/to/factory",
          "agents": {
            "blueprint": { "model": "openai/gpt-5.4", "timeout": 600 },
            "wrench": { "model": "zai/glm-5-turbo", "timeout": 1200 },
            "scope": { "model": "minimax/MiniMax-M2.7", "timeout": 600 },
            "beaker": { "model": "openai/gpt-5.4-mini", "timeout": 600 },
            "wrenchSr": { "model": "gemini/gemini-3.1-pro-preview", "timeout": 1200 }
          },
          "escalation": {
            "wrenchSrAfterRound": 3,
            "chrisAfterRound": 5
          }
        }
      }
    }
  }
}
```

### Config Options

| Key | Default | Description |
|-----|---------|-------------|
| `repoPath` | `.` | Target repository path |
| `factoryPath` | `factory` | Factory directory with prompts and state |
| `pythonCommand` | `railclaw-pipeline` | Python CLI command |
| `stateDir` | `.pipeline-state` | State file directory |
| `eventsDir` | `.pipeline-events` | Event log directory |
| `agents.*` | — | Per-agent model and timeout |
| `timing.geminiPollInterval` | `60` | Seconds between Gemini review polls |
| `timing.approvalTimeout` | `86400` | Max seconds to wait for human approval |
| `pm2.processName` | `railclaw-mc` | PM2 process name for deploy |
| `escalation.wrenchSrAfterRound` | `3` | Switch to Wrench Sr after N fix rounds |
| `escalation.chrisAfterRound` | `5` | Escalate to Chris after N fix rounds |

## Usage

The plugin registers a `pipeline_run` tool with these actions:

- **`run`** — Start a new pipeline for an issue or milestone
- **`status`** — Check current pipeline state
- **`resume`** — Resume an interrupted pipeline
- **`abort`** — Cancel the active pipeline

### Hotfix Mode

Set `hotfix: true` to run post-hoc review on a direct-to-main hotfix. Bypasses stages 1-2.5, runs Scope review on the diff, creates a follow-up PR if findings exist.

## Architecture

```
railclaw-pipeline/
  src/                          # TypeScript plugin layer (~50-100 lines logic)
    index.ts                    # definePluginEntry — registration + wiring
    tool.ts                     # api.registerTool() + TypeBox parameter schema
    config.ts                   # Plugin config normalization
    python-bridge.ts            # Subprocess wrapper around Python CLI
    store.ts                    # Runtime store for active run metadata
    hooks.ts                    # Lifecycle hooks (startup/shutdown)
  python/
    src/railclaw_pipeline/
      cli.py                    # Click CLI: run, status, resume, abort
      config.py                 # Settings from env/CLI/plugin config
      pipeline.py               # Stage runner orchestrator
      state/                    # Pydantic models + atomic JSON persistence
      runner/                   # Agent subprocess execution
      stages/                   # Pipeline stage implementations
      github/                   # Git/gh CLI wrappers
      prompts/                  # Jinja2 template loader (sandboxed)
      events/                   # JSON lines event emitter
      milestone/                # Multi-issue milestone mode
```

### Pipeline Stages

| Stage | Agent | Description |
|-------|-------|-------------|
| 0 — Preflight | — | Environment checks |
| 1 — Blueprint | Blueprint | Planning |
| 2 — Wrench | Wrench | Implementation |
| 2.5 — PR | — | PR creation |
| 3 — Audit | Scope | Completeness audit |
| 3.5 — Audit Fix | Wrench | Fix audit findings |
| 4 — Review | Scope | Code review |
| 5 — Fix Loop | Wrench/Wrench Sr | Fix review findings (max 5 rounds) |
| Cycle 2 | Gemini | External review loop |
| 7 — Docs | Quill | Documentation (opt-in) |
| 8 — Approval | — | Human approval gate |
| 8c — Merge | — | Squash merge + branch delete |
| 9 — Deploy | — | PM2 restart + health check |
| 10 — QA | Beaker | QA sweep |
| 11 — Hotfix | Scope/Wrench | Post-hoc hotfix review |
| 12 — Lessons | — | Lessons learned generation |

### Security

- `shell=False` on all subprocess calls
- Jinja2 sandboxed templates (SSTI protection)
- Branch name sanitization before use in commands/paths
- No secrets in state files or event logs

## Development

```bash
npm install                    # Install JS deps + Python package
npm run build                  # TypeScript compile
npm run test                   # JS tests
npm run test:py                # Python tests
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

## License

MIT
