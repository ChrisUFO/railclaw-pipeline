# Architecture: RailClaw Pipeline

## Overview

RailClaw Pipeline is an OpenClaw plugin that automates a multi-stage coding factory workflow: planning, implementation, review, merge, deploy, and QA. It follows a **thin TypeScript / heavy Python** architecture.

```
┌──────────────────────────────────────────────────┐
│                 OpenClaw Gateway                  │
│  ┌─────────────────────────────────────────────┐ │
│  │           Plugin (TypeScript)                │ │
│  │  index.ts → tool.ts → python-bridge.ts      │ │
│  │       ↕               ↕                     │ │
│  │  store.ts         config.ts                  │ │
│  │       ↕               ↕                     │ │
│  │  hooks.ts          .pipeline.json            │ │
│  └──────────────┬──────────────────────────────┘ │
└─────────────────┼────────────────────────────────┘
                  │ spawn / JSON stdout
                  ▼
┌──────────────────────────────────────────────────┐
│            Python CLI (Click)                     │
│  cli.py → pipeline.py → stages/*                 │
│       ↕           ↕          ↕                   │
│  state/      events/      runner/                │
│  models.py   emitter.py   agent.py               │
│  persist.py  console.py   subprocess_runner.py   │
│  lock.py     notifications.py                    │
│       ↕                                          │
│  github/  prompts/  milestone/  utils/           │
└──────────────────────────────────────────────────┘
```

## Process Model

### Blocking Mode (default for direct CLI usage)

The Python CLI runs the full pipeline synchronously. The TS bridge waits for process exit and parses JSON stdout.

```
TS: spawn(python, ["run", ...]) → wait → parse JSON → resolve
                                        ↕
Python: run_stage × N → write state.json → output result → exit
```

### Detached Mode (default for plugin invocations)

The Python CLI double-forks into a background daemon. The parent outputs a `{"ok": true, "status": "started", "pid": N}` JSON and exits immediately. The child continues pipeline execution.

```
TS: spawn(python, ["run", "--detach", ...]) → immediate resolve
                                               ↕
Python parent: fork → output started JSON → exit
Python child:  run_stage × N → write state.json → exit

TS: spawn(python, ["status"]) → read state.json → return current state
TS: spawn(python, ["abort"])  → read PID → SIGTERM → update state
```

### Process Lifecycle

```
                  ┌──────────┐
                  │  idle    │
                  └────┬─────┘
                       │ run --detach
                       ▼
              ┌────────────────┐
              │ running (fork) │─── gateway restart ───▶ re-associate via PID
              └──┬─────┬──────┘
                 │     │
         stage_end   SIGTERM / abort
                 │     │
                 ▼     ▼
          ┌──────────┐ ┌─────────────┐
          │ completed│ │ failed/     │
          └──────────┘ │ interrupted │
                       └─────────────┘
```

**PID file**: `{stateDir}/pipeline.pid` — contains the daemon child PID. Used for:

- Abort: read PID → SIGTERM → wait → update state
- Status: if state says "running" but PID is dead → mark "interrupted"
- Gateway restart: check PID aliveness, re-associate or mark interrupted

**Lock file**: `{stateDir}/pipeline.lock` — JSON with `{pid, timestamp, agent, stage, run_id}`.
Cross-platform PID-based validation (no fcntl). Stale detection via PID liveness + age threshold
(default 4 hours). Force override available. Atomic writes via tempfile + os.replace.

**Circuit breaker**: `{stateDir}/circuit_breaker.json` — tracks consecutive agent timeouts.
After 2 consecutive timeouts for the same agent, the circuit opens and the pipeline escalates
instead of retrying. Reset on successful agent execution.

### Pre-Flight Validation Gate

Runs **before Stage 0** (before any state is written) as a hard gate. All 7 checks run
regardless of individual failures — all failures reported at once:

1. `gh auth status` — GitHub CLI authenticated
2. Python venv + `railclaw-pipeline --help` reachable
3. All configured agent CLIs reachable (opencode, gemini)
4. Repo path exists, is git repo, clean working tree
5. Disk space > 500MB (configurable)
6. State directory writable
7. No active pipeline lock (uses StateLock)

Skipped for `resume` action (already validated on initial run). Configurable via
`preflight` section in pipeline config.

### Crash Recovery

`railclaw-pipeline repair [--fix] [--force]` detects and repairs broken pipeline state:

- **Stale lock**: dead PID or age > 5 min → remove
- **Orphaned branches**: feat/issue-_ or fix/issue-_ with no open PR → delete
- **Uncommitted changes**: working tree modifications → stash
- **Corrupt state**: invalid JSON → archive to `.pipeline-state/corrupt/`
- **Missing PR**: state says 2.5+ complete but PR gone → flag for manual review
- **Dangling processes**: pipeline subprocess still running → kill

## Data Flow

### State Persistence

All pipeline state is persisted to `state.json` via atomic writes (tempfile + fsync + os.replace):

```
PipelineState (Pydantic)
├── issue_number: int
├── stage: PipelineStage (StrEnum)
├── status: PipelineStatus (running|paused|completed|failed)
├── pid: int | None          ← daemon PID (detached mode)
├── pr_number: int | None
├── branch: str | None
├── timestamps: Timestamps
│   ├── started: datetime
│   ├── stage_entered: datetime
│   └── last_updated: datetime
├── cycle: CycleState
│   ├── cycle1_round: int
│   ├── cycle2_round: int
│   ├── scope_verdict: str
│   └── gemini_clean: bool
├── findings: {current: [], history: []}
├── error: {category, message, stage} | None
└── retry_count: int
```

**State path**: `{factoryPath}/{stateDir}/state.json`
**PID path**: `{factoryPath}/{stateDir}/pipeline.pid`

### Event Log

Append-only JSON lines file at `{eventsDir}/events.jsonl`. Rotated at 10MB with 3 archives.

```json
{"ts": "2025-01-15T10:30:00Z", "type": "stage_start", "issue": 25, "stage": "stage1_blueprint"}
{"ts": "2025-01-15T10:35:00Z", "type": "stage_end", "issue": 25, "stage": "stage1_blueprint", "duration_s": 300, "payload": {"success": true}}
```

### Notification Log

Append-only JSON lines file at `{eventsDir}/notifications.jsonl`. Written at every stage transition. Rotated at 10MB.

```json
{
  "ts": "2025-01-15T10:35:00Z",
  "type": "stage_end",
  "issue": 25,
  "stage": "stage1_blueprint",
  "duration_s": 300,
  "verdict": "pass",
  "findings_count": 0,
  "next_stage": "stage2_wrench"
}
```

Queried via `railclaw-pipeline notifications [--since <iso8601>]`.

### Runtime Store (TypeScript)

In-memory store for active run metadata. Advisory — disk state is authoritative.

```
PipelineRunMetadata
├── issueNumber: number
├── stage: string
├── status: string
├── startedAt: string (ISO)
├── updatedAt: string (ISO)
├── statePath: string
└── pid: number | null
```

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
| Cycle 2         | Scope/Gemini     | External review loop (max 20 rounds) | 20m     |
| 7 — Docs        | Quill            | Documentation (opt-in)               | 10m     |
| 8 — Approval    | —                | Human approval gate                  | 24h     |
| 8c — Merge      | —                | Squash merge + branch delete         | 2m      |
| 9 — Deploy      | —                | PM2 restart + health check           | 5m      |
| 10 — QA         | Beaker           | QA sweep                             | 10m     |
| 11 — Hotfix     | Scope/Wrench     | Post-hoc hotfix review               | 30m     |
| 12 — Lessons    | —                | Lessons learned generation           | 2m      |

### Stage Runner Pattern

Every stage follows the same pattern in `run_stage()`:

1. Update state (stage, status=running, stage_entered timestamp)
2. Atomic save state
3. Emit `stage_start` event + write stage handoff notification
4. Update checkpoint
5. Execute stage handler with timeout
6. On success: emit `stage_end` + notification, update board, save state
7. On failure: emit `stage_end` with error, re-raise

### Fix Loop Escalation

```
Round 1-2: Wrench fixes Scope findings
Round 3+:  Escalate to Wrench Sr (gemini CLI)
Round 5:   Escalate to Chris (emit event)
```

### Cycle 2 Convergence

```
while not gemini_clean and cycle2_round < 20:
    Scope re-review → Wrench fix → Gemini review poll
    If findings stall (2 rounds no reduction) → emit warning
    If 20 rounds exhausted → FatalPipelineError
```

## TypeScript Plugin Layer

### File Responsibilities

| File               | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `index.ts`         | `definePluginEntry` — wires tool + config + hooks  |
| `tool.ts`          | `api.registerTool()` with TypeBox schema           |
| `python-bridge.ts` | Subprocess spawn + JSON parsing + PID tracking     |
| `config.ts`        | Config normalization + `.pipeline.json` resolution |
| `store.ts`         | Runtime store for active run metadata              |
| `hooks.ts`         | Gateway lifecycle hooks (start/stop)               |

### Tool Actions

| Action          | Description                                           |
| --------------- | ----------------------------------------------------- |
| `run`           | Start pipeline (detached by default when from plugin) |
| `status`        | Read state.json, return current stage/status          |
| `resume`        | Resume interrupted pipeline                           |
| `abort`         | Kill daemon process + mark failed                     |
| `notifications` | Query pending stage handoff notifications             |
| `repair`        | Detect and fix broken pipeline state                  |

### Config Resolution Chain

```
Plugin defaults (DEFAULT_CONFIG)
  ↓ merge
User config (openclaw.json → api.pluginConfig)
  ↓ merge
Repo config (.pipeline.json walked up from repoPath)
  ↓ result
Runtime config (buildRuntimeConfig)
```

## Python Package

### Dependencies

- **click** — CLI framework
- **pydantic** — State model validations
- **jinja2** — Sandboxed prompt templates
- **rich** — Console progress output
- **tenacity** — Retry logic

All pure-Python, no native extensions (ARM64 compatible).

### Module Map

```
railclaw_pipeline/
├── cli.py              Click CLI: run, status, resume, abort, notifications, cleanup, repair
├── config.py           PipelineConfig from env/CLI/plugin config
├── pipeline.py         Stage runner orchestrator + run_stage()
├── state/
│   ├── models.py       Pydantic: PipelineStage, PipelineStatus, PipelineState
│   ├── persistence.py  Atomic JSON load/save (tempfile+fsync+os.replace)
│   ├── lock.py         Cross-platform advisory lock (PID-based, JSON format)
│   └── pid.py          PID file management (read/write/is_alive/kill)
├── runner/
│   ├── agent.py        AgentConfig, AgentRunner, AgentResult
│   ├── agent_config.py Centralized agent config builder
│   └── subprocess_runner.py  asyncio subprocess wrappers + SIGTERM→SIGKILL cascade
├── stages/             One file per pipeline stage
├── events/
│   ├── emitter.py      JSON lines event writer + notification writer
│   ├── console.py      Rich console output
│   └── notifications.py Notification read/query/rotation
├── github/
│   ├── git.py          Git operations (shell=False)
│   ├── gh.py           GitHub API via gh CLI
│   ├── pr.py           PR create (with --body-file), view, merge, comment
│   ├── review.py       Gemini review polling
│   ├── board.py        Board JSON update helpers
│   └── checkpoint.py   CHECKPOINT.md helpers
├── milestone/
│   ├── collector.py    Multi-issue collection from gh
│   └── runner.py       Sequential issue execution
├── prompts/
│   ├── loader.py       Sandboxed Jinja2 template loader
│   └── templates/      Bundled .j2 prompt templates
├── validation/
│   ├── preflight.py    7-check pre-flight validation gate
│   ├── circuit_breaker.py  Agent timeout circuit breaker
│   └── repair.py       Crash recovery and state repair engine
└── utils/
    └── cleanup.py      Old run log cleanup
```

## Security

- **shell=False** on all subprocess calls — list args only, never string commands
- **Jinja2 SandboxedEnvironment** — SSTI protection at the template engine level
- **Branch name sanitization** — UNSAFE_BRANCH_CHARS regex before use in paths/commands
- **No secrets in state files or event logs** — credentials stay in environment variables
- **Atomic file writes** — tempfile + fsync + os.replace prevents corruption on crash
- **Template path traversal prevention** — `..` and `/` prefixes rejected in template names
- **Cross-platform lock** — PID-based validation replaces POSIX-only fcntl
- **Timeout cascade prevention** — SIGTERM→SIGKILL cascade prevents stuck processes

## Testing

### TypeScript (Vitest)

```
tests/
├── __mocks/
│   ├── plugin-entry.ts    Mock for openclaw/plugin-sdk
│   └── runtime-store.ts   Mock for createPluginRuntimeStore
├── config.test.ts         Config normalization
├── config-resolve.test.ts .pipeline.json resolution
├── plugin-entry.test.ts   Plugin registration
├── python-bridge.test.ts  Subprocess spawn + JSON parsing
└── tool-schema.test.ts    TypeBox schema validation
```

### Python (pytest)

```
python/tests/
├── test_cli.py                 CLI commands
├── test_emitter.py             Event emission + rotation
├── test_notifications.py       Notification read/write/query
├── test_pid_management.py      PID file lifecycle
├── test_persistence.py         State load/save + crash recovery
├── test_state_models.py        Pydantic model validation
├── test_agent_runner.py        Agent execution
├── test_subprocess_runner.py   Async subprocess wrappers
├── test_subprocess_kill.py     SIGTERM→SIGKILL cascade on timeout
├── test_git.py                 Git operations
├── test_gh.py                  GitHub CLI wrapper
├── test_review_parsing.py      Gemini review parsing
├── test_stage_preflight.py     Preflight checks (Stage 0)
├── test_preflight_gate.py      Pre-flight validation gate (7 checks)
├── test_stage5_fix_loop.py     Fix loop + escalation
├── test_stage_pr.py            PR creation + body format
├── test_stage_merge.py         Merge execution
├── test_gemini_polling.py      Gemini review polling
├── test_approval_gate.py       Approval workflow
├── test_milestone.py           Multi-issue mode
├── test_resume.py              Kill-and-resume scenarios
├── test_injection_resistance.py SSTI + command injection
├── test_cleanup.py             Run log cleanup
├── test_lock.py                Cross-platform StateLock lifecycle
├── test_circuit_breaker.py     Agent timeout circuit breaker
└── test_repair.py              Crash recovery and state repair
```

### Running Tests

```bash
npm run test          # TypeScript (Vitest)
npm run test:py       # Python (pytest)
npm run lint          # ESLint
npm run lint:py       # Ruff
npm run typecheck     # tsc --noEmit
```
