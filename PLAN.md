# PLAN.md — railclaw-pipeline: OpenClaw Plugin for Automated Code Factory

**Repo:** https://github.com/ChrisUFO/railclaw-pipeline
**Issue:** ChrisUFO/RailClaw#71
**Design Doc:** `factory/pipeline-orchestrator-design.md` in RailClaw repo (reference only — this plugin IS the implementation)

## 1. Goal

Build a publishable OpenClaw plugin (`openclaw plugins install chrisufo/railclaw-pipeline`) that automates the full coding factory pipeline: planning → implementation → review → merge → deploy → QA. Installed as a standalone plugin, configured per-repo.

## 2. Non-Negotiable Architecture

- [ ] **OpenClaw plugin** — `definePluginEntry`, `api.registerTool()`, `openclaw.plugin.json` manifest
- [ ] **Thin TS wrapper** — plugin registration, config loading, TypeBox parameter validation, subprocess invocation, JSON result mapping. ~50-100 lines of actual logic.
- [ ] **Heavy Python logic** — state machine, stage runner, agent execution, git/GitHub integration, review loops, deploy flow, hotfix flow, milestone mode
- [ ] **Python subprocess bridge** — TS spawns Python CLI, parses JSON stdout
- [ ] **Focused SDK imports** — `openclaw/plugin-sdk/plugin-entry`, `runtime-store`, `config-runtime`, etc. Never deprecated root import.
- [ ] **TypeBox schemas** — tool parameters validated at the TS layer
- [ ] **Python 3.11+** on ARM64 — pure-Python deps where possible, no native extensions
- [ ] **Generic/configurable** — repo paths, agent configs, factory paths all come from `plugins.entries.railclaw-pipeline.config` in `openclaw.json`. Not hardcoded to RailClaw.
- [ ] **ClawHub publishable** — `clawhub package publish chrisufo/railclaw-pipeline`

## 3. Repo Layout

```
railclaw-pipeline/
  package.json                          # npm package + "openclaw" metadata
  openclaw.plugin.json                  # plugin manifest
  tsconfig.json
  .gitignore
  README.md                             # install guide, config reference, architecture overview
  src/
    index.ts                            # definePluginEntry — registration + wiring
    tool.ts                             # api.registerTool() + TypeBox parameter schema
    config.ts                           # plugin config normalization from api.pluginConfig
    python-bridge.ts                    # subprocess wrapper around railclaw-pipeline CLI
    store.ts                            # createPluginRuntimeStore for active run metadata
    hooks.ts                            # lifecycle hooks (api.on)
  python/
    src/railclaw_pipeline/
      __init__.py
      cli.py                            # Click CLI: run, status, resume, abort
      config.py                         # Settings from env/CLI args/plugin config
      state/
        __init__.py
        models.py                       # Pydantic: PipelineStage, PipelineStatus, PipelineState
        persistence.py                  # Atomic JSON state.json (tempfile+fsync+os.replace)
        lock.py                         # File-based lock for concurrent access
      runner/
        __init__.py
        agent.py                        # AgentConfig, AgentRunner, AgentResult
        subprocess_runner.py            # asyncio.create_subprocess_exec wrappers
      stages/
        __init__.py
        stage0_preflight.py             # Environment checks
        stage1_blueprint.py             # Planning
        stage2_wrench.py                # Implementation
        stage2_5_pr.py                  # PR creation
        stage3_audit.py                 # Scope completeness audit
        stage3_5_fix.py                 # Wrench audit fixes
        stage4_review.py                # Scope code review
        stage5_fix_loop.py              # Cycle 1 fix rounds + escalation
        cycle2_gemini.py                # Gemini review polling + Wrench Sr fallback
        stage7_docs.py                  # Quill docs (opt-in)
        stage8_approval.py              # Human approval gate
        stage8c_merge.py                # Pre-merge validation + merge
        stage9_deploy.py                # PM2 restart + health check
        stage10_qa.py                   # Beaker QA sweep
        stage11_hotfix.py               # Hotfix review
        stage12_lessons.py              # Lessons learned generation
      github/
        __init__.py
        git.py                          # Git operations (branch, checkout, push, etc.)
        gh.py                           # GitHub API via gh CLI
        pr.py                           # PR create, view, merge, comment
        review.py                       # Gemini review polling (comments + reviews + body)
        board.py                        # Board JSON update helpers
        checkpoint.py                   # CHECKPOINT.md helpers
      milestone/
        __init__.py
        collector.py                    # Multi-issue collection from gh
        runner.py                       # Sequential issue execution
      prompts/
        __init__.py
        loader.py                       # Jinja2 template loader from factory/ dir
        templates/                      # .j2 files for each agent/stage
      events/
        __init__.py
        emitter.py                      # Event log writer (JSON lines)
        console.py                      # Rich progress output
    pyproject.toml                      # Python 3.11+, dependencies
    requirements.txt                    # Pinned deps for reproducibility
    tests/
      test_state_models.py
      test_persistence.py
      test_agent_runner.py
      test_subprocess_runner.py
      test_git.py
      test_gh.py
      test_review_parsing.py
      test_stage_preflight.py
      test_stage5_fix_loop.py
      test_gemini_polling.py
      test_approval_gate.py
      test_milestone.py
      test_cli.py
      test_injection_resistance.py      # SSTI + command injection regression
      test_resume.py                    # Kill-and-resume scenarios
  tests/
    plugin-entry.test.ts                # Plugin loads, registers tool
    tool-schema.test.ts                 # TypeBox parameter validation
    python-bridge.test.ts               # Subprocess spawn + JSON parsing
    config.test.ts                      # Config normalization
  scripts/
    postinstall.sh                      # pip install -e ./python on npm install
```

## 4. Plugin Contract

### 4.1 Tool registration

```typescript
api.registerTool({
  name: "pipeline_run",
  description: "Run the coding factory pipeline for an issue or milestone",
  parameters: Type.Object({
    action: Type.Union([Type.Literal("run"), Type.Literal("status"), Type.Literal("resume"), Type.Literal("abort")]),
    issueNumber: Type.Optional(Type.Number()),
    milestone: Type.Optional(Type.String()),
    hotfix: Type.Optional(Type.Boolean()),
    forceStage: Type.Optional(Type.String()),
    waitForCompletion: Type.Optional(Type.Boolean()),
  }),
  async execute(_id, params) {
    // Validate cross-field constraints
    // Spawn: python-bridge → railclaw-pipeline <action> [options]
    // Parse JSON stdout → tool response
  },
});
```

### 4.2 JSON bridge contract

Python CLI stdout must be valid JSON with these fields:

```json
{
  "ok": true,
  "action": "run",
  "stage": "stage4_review",
  "status": "in_progress",
  "issueNumber": 71,
  "prNumber": 72,
  "branch": "feat/issue-71-pipeline-orchestrator",
  "message": "Scope review in progress",
  "statePath": "/path/to/state.json",
  "error": null
}
```

### 4.3 Plugin config shape

Configured in `openclaw.json` under `plugins.entries.railclaw-pipeline.config`:

```json
{
  "repoPath": "/path/to/target/repo",
  "factoryPath": "/path/to/factory/dir",
  "pythonCommand": "railclaw-pipeline",
  "stateDir": ".pipeline-state",
  "eventsDir": ".pipeline-events",
  "agents": {
    "blueprint": { "model": "openai/gpt-5.4", "timeout": 600 },
    "wrench": { "model": "zai/glm-5-turbo", "timeout": 1200 },
    "scope": { "model": "minimax/MiniMax-M2.7", "timeout": 600 },
    "beaker": { "model": "openai/gpt-5.4-mini", "timeout": 600 },
    "wrenchSr": { "model": "gemini/gemini-3.1-pro-preview", "timeout": 1200 }
  },
  "timing": {
    "geminiPollInterval": 60,
    "approvalTimeout": 86400,
    "healthCheckTimeout": 30
  },
  "pm2": {
    "processName": "railclaw-mc",
    "ecosystemPath": "ecosystem.config.cjs"
  },
  "escalation": {
    "wrenchSrAfterRound": 3,
    "chrisAfterRound": 5
  }
}
```

## 5. Implementation Phases

### Phase 1: Plugin Skeleton + Python Foundation

**Goal:** Ship an installable-but-noop plugin with Python state machine foundation.

#### Deliverables
- [ ] `package.json` with `openclaw` metadata, `@sinclair/typebox` dep
- [ ] `openclaw.plugin.json` manifest (id, name, extensions, compat, build)
- [ ] `tsconfig.json` targeting ES2022+, ESM
- [ ] `src/index.ts` — `definePluginEntry` with no-op tool registration
- [ ] `src/tool.ts` — `pipeline_run` tool with TypeBox schema, returns "not implemented" yet
- [ ] `src/config.ts` — reads `api.pluginConfig`, normalizes with defaults
- [ ] `src/python-bridge.ts` — placeholder that spawns Python with `--help`
- [ ] `python/pyproject.toml` — Python 3.11+, click, pydantic, jinja2, rich, tenacity
- [ ] `python/src/railclaw_pipeline/cli.py` — Click CLI with run/status/resume/abort stubs
- [ ] `python/src/railclaw_pipeline/config.py` — Settings from env + CLI args
- [ ] `python/src/railclaw_pipeline/state/models.py` — PipelineStage, PipelineStatus, PipelineState (Pydantic)
- [ ] `python/src/railclaw_pipeline/state/persistence.py` — Atomic load/save (tempfile+fsync+os.replace)
- [ ] `python/src/railclaw_pipeline/state/lock.py` — File-based advisory lock
- [ ] `python/src/railclaw_pipeline/events/emitter.py` — JSON lines event writer
- [ ] `python/src/railclaw_pipeline/events/console.py` — Rich progress output
- [ ] `scripts/postinstall.sh` — pip install in Python dir
- [ ] `README.md` — install instructions, quickstart, config reference
- [ ] `.gitignore` (node_modules, __pycache__, .pyc, dist, state.json)

#### Tests
- [ ] Plugin loads and registers tool without errors
- [ ] TypeBox schema accepts valid inputs, rejects invalid
- [ ] Python state models serialize/deserialize correctly
- [ ] Atomic persistence survives crash mid-write
- [ ] CLI --help exits 0

#### Depends on: Nothing

### Phase 2: Agent Runner + Subprocess Execution

**Goal:** Python can spawn and monitor agent subprocesses; TS bridge is functional.

#### Deliverables
- [ ] `python/src/railclaw_pipeline/runner/agent.py` — AgentConfig, AgentRunner, AgentResult
- [ ] `python/src/railclaw_pipeline/runner/subprocess_runner.py` — asyncio subprocess with timeout, kill, capture
- [ ] `src/python-bridge.ts` — real implementation: spawn Python, parse JSON, map to tool response
- [ ] `src/store.ts` — runtime store for active run metadata
- [ ] Support `opencode run --dir ... --agent build` and `gemini` CLI subprocess patterns
- [ ] Verdict parsing: pass, revision, needs-human, timeout, error
- [ ] `shell=False` everywhere, list args only

#### Tests
- [ ] AgentRunner: success, timeout, cancellation, non-zero exit
- [ ] Verdict parsing for all outcomes
- [ ] Python bridge: valid JSON → tool response, malformed → error
- [ ] Runtime store round-trip

#### Depends on: Phase 1

### Phase 3: Git + GitHub Operations

**Goal:** Safe git/gh wrappers for branch management, PR creation, review polling.

#### Deliverables
- [ ] `python/src/railclaw_pipeline/github/git.py` — branch create/checkout/fetch/push/pull/clean
- [ ] `python/src/railclaw_pipeline/github/gh.py` — gh CLI wrapper with timeout
- [ ] `python/src/railclaw_pipeline/github/pr.py` — create, view, merge, comment, list
- [ ] `python/src/railclaw_pipeline/github/review.py` — poll /comments + /reviews, parse `<details>` blocks, track last-processed timestamp
- [ ] `python/src/railclaw_pipeline/github/board.py` — board.json read/update helpers
- [ ] `python/src/railclaw_pipeline/github/checkpoint.py` — CHECKPOINT.md create/read/sign-off/archive
- [ ] All wrappers use list args, no shell=True, handle timeouts

#### Tests
- [ ] Git wrapper: stdout/stderr propagation, timeout handling
- [ ] gh wrapper: non-zero exit, cancellation
- [ ] Review parsing: inline comments, body `<details>`, clean detection, stale timestamp filtering
- [ ] Branch name injection resistance (hostile characters)

#### Depends on: Phase 2

### Phase 4: Stages 0–2.5 (Preflight → Blueprint → Wrench → PR)

**Goal:** End-to-end pipeline run from issue number to open PR.

#### Deliverables
- [ ] `python/src/railclaw_pipeline/stages/stage0_preflight.py` — environment readiness checks
- [ ] `python/src/railclaw_pipeline/stages/stage1_blueprint.py` — fetch issue, invoke Blueprint, write PLAN.md
- [ ] `python/src/railclaw_pipeline/stages/stage2_wrench.py` — implement per PLAN.md phases
- [ ] `python/src/railclaw_pipeline/stages/stage2_5_pr.py` — gh pr create with Closes #N
- [ ] `python/src/railclaw_pipeline/prompts/loader.py` — Jinja2 template loader from factory/ dir
- [ ] `python/src/railclaw_pipeline/prompts/templates/` — .j2 files for Blueprint, Wrench prompts
- [ ] State persistence at every stage transition
- [ ] No Gemini trigger at Stage 2.5 (auto-review only)

#### Tests
- [ ] Preflight: wrong branch, dirty tree, missing tools
- [ ] Stage 1: PLAN.md written safely
- [ ] Stage 2.5: idempotent if PR exists
- [ ] Integration: dry-run preflight through PR creation with stubbed subprocesses

#### Depends on: Phase 3

### Phase 5: Stages 3–5 (Audit → Review → Fix Loop)

**Goal:** Cycle 1 internal review with Scope and Wrench fix rounds.

#### Deliverables
- [ ] `stages/stage3_audit.py` — Scope completeness audit, findings-only output
- [ ] `stages/stage3_5_fix.py` — Wrench audit fixes (all findings verbatim)
- [ ] `stages/stage4_review.py` — Scope code review with structured verdict
- [ ] `stages/stage5_fix_loop.py` — max 5 rounds, fresh Scope sessions, escalation at R3 (Wrench Sr) and R5 (Chris)
- [ ] Findings persistence in state (current + history)
- [ ] Audit-clean skip path (zero findings → skip Stage 3.5)
- [ ] Triage enforcement: completeness/hardening mandatory, polish discretionary
- [ ] Board and checkpoint updates on every transition

#### Tests
- [ ] Audit-clean skip path
- [ ] Fix loop terminates on PASS
- [ ] Escalation triggers at R3 and R5
- [ ] Triage: non-polish treated as mandatory regardless of reviewer disposition
- [ ] Integration: Scope → Wrench → Scope loop with stubbed subprocesses

#### Depends on: Phase 4

### Phase 6: Cycle 2 (Gemini Review Loop)

**Goal:** Gemini review polling, findings extraction, convergence detection.

#### Deliverables
- [ ] `stages/cycle2_gemini.py` — full Gemini review loop
- [ ] Poll BOTH `/pulls/N/comments` AND `/pulls/N/reviews`
- [ ] Parse review body for `<details>` blocks
- [ ] Clean detection: new formal review + zero new findings
- [ ] Stale timestamp tracking (don't reprocess old findings)
- [ ] Wrench Sr. fallback when Gemini unavailable
- [ ] Non-convergence → Chris escalation
- [ ] Safety cap (20 rounds)
- [ ] Scope re-review after each Wrench fix before Gemini re-review

#### Tests
- [ ] Review body `<details>` parsing
- [ ] Zero-inline but findings-in-body scenario
- [ ] Clean review detection
- [ ] Stale timestamp filtering
- [ ] Gemini timeout → Wrench Sr. escalation
- [ ] Integration: Scope-clear → Gemini loop across rounds

#### Depends on: Phase 5

### Phase 7: Stages 8–8c (Approval + Merge)

**Goal:** Human approval gate and merge execution.

#### Deliverables
- [ ] `stages/stage8_approval.py` — approval wait via file protocol (awaiting-approval.json, approve-N.json, abort-N.json)
- [ ] `stages/stage8c_merge.py` — pre-merge validation (mergeable, CI), squash merge, branch delete
- [ ] Merge summary generation (commits, findings, deferred items)
- [ ] Approval timeout handling
- [ ] Abort path → pipeline status = failed

#### Tests
- [ ] Approval file detected and consumed safely
- [ ] Abort changes status to failed
- [ ] Pre-merge mergeability failure blocks merge
- [ ] Approval timeout behavior

#### Depends on: Phase 6

### Phase 8: Stages 7, 9–12 (Docs → Deploy → QA → Hotfix → Lessons)

**Goal:** Post-merge pipeline completion.

#### Deliverables
- [ ] `stages/stage7_docs.py` — Quill docs (opt-in, `docs:` commit prefix)
- [ ] `stages/stage9_deploy.py` — git pull, npm ci --production, PM2 restart, health check
- [ ] `stages/stage10_qa.py` — Beaker QA, PLAN completeness check, critical issue auto-file
- [ ] `stages/stage11_hotfix.py` — direct-to-main hotfix with follow-up PR
- [ ] `stages/stage12_lessons.py` — lessons learned entry, checkpoint archival
- [ ] Board update on completion

#### Tests
- [ ] Quill skip vs run logic
- [ ] Deploy health check success and timeout
- [ ] Beaker critical finding auto-files GitHub issue
- [ ] Hotfix creates follow-up PR when findings exist
- [ ] Lessons entry includes result, duration, deferred items, violations

#### Depends on: Phase 7

### Phase 9: Milestone Mode

**Goal:** Multi-issue Blueprint planning, sequential execution.

#### Deliverables
- [ ] `milestone/collector.py` — `gh issue list --milestone ...` collection
- [ ] `milestone/runner.py` — sequential per-issue execution with state reset between issues
- [ ] Single Blueprint planning run for all milestone issues
- [ ] Unified PLAN.md with per-issue phase breakdowns
- [ ] `git checkout main && git pull` between issue runs
- [ ] Scaled Blueprint timeout for milestone size

#### Tests
- [ ] Milestone issue collection and parsing
- [ ] Multi-issue plan parsing
- [ ] Sequential per-issue state reset
- [ ] Integration: two-issue milestone with stubbed stages

#### Depends on: Phase 8

### Phase 10: OpenClaw Integration + Observability

**Goal:** Plugin is fully wired into OpenClaw lifecycle, observable, resumable.

#### Deliverables
- [ ] `src/hooks.ts` — lifecycle hooks (startup/shutdown cleanup)
- [ ] `src/index.ts` — final wiring: tool + config + store + hooks
- [ ] Plugin survives OpenClaw restarts (re-reads state from disk)
- [ ] Status/resume/abort work via runtime store + disk state
- [ ] Tool responses include: stage, status, PR#, next operator action
- [ ] Event hooks for orchestration visibility (only additive, no logic in hooks)

#### Tests
- [ ] Plugin registers tool with correct name/schema
- [ ] Config normalization from plugin config
- [ ] Runtime store round-trip
- [ ] Lifecycle hook registration smoke test
- [ ] Integration: openclaw-side smoke test invoking pipeline_run against stub Python

#### Depends on: Phase 9

### Phase 11: Hardening + Pi Validation

**Goal:** Production-ready on Raspberry Pi ARM64.

#### Deliverables
- [ ] Kill-and-resume for every long-running stage
- [ ] state.json valid after forced interruption during save
- [ ] Zombie process prevention (all subprocess types)
- [ ] SSTI protections in Jinja2 (sandboxed)
- [ ] Command-injection resistance (hostile branch names, issue bodies)
- [ ] Wrench resume cleanup safety
- [ ] Plugin restart with active pipeline on disk
- [ ] ARM64 validation on Pi with Python 3.11+
- [ ] Resource usage measurement (idle + active)
- [ ] Release checklist

#### Tests
- [ ] Crash/restart resume
- [ ] SSTI + command injection regression
- [ ] State corruption and temp-file cleanup
- [ ] Plugin bridge handles Python crash + partial stdout
- [ ] End-to-end: issue run through plugin tool on Pi

#### Depends on: Phase 10

## 6. Cross-Cutting Requirements

### Python / Pi
- [ ] Python 3.11+ runtime target
- [ ] Pure-Python deps where possible (no ARM64 native extensions)
- [ ] Atomic file writes for all recovery-critical state
- [ ] Low idle resource usage suitable for Raspberry Pi

### OpenClaw Plugin
- [ ] Focused `openclaw/plugin-sdk/<subpath>` imports only
- [ ] TypeBox-validated tool parameters
- [ ] Plugin config from `plugins.entries.railclaw-pipeline.config`
- [ ] Runtime store is advisory; disk state is authoritative

### Pipeline Correctness
- [ ] Board update at every stage transition
- [ ] CHECKPOINT.md update before leaving every stage
- [ ] Findings verbatim to Wrench — never pre-validate
- [ ] Poll both Gemini endpoints + read review bodies
- [ ] Stages 9–12 always execute after successful merge

### Security
- [ ] `shell=False` on all subprocesses
- [ ] Jinja2 sandboxed (SSTI protection)
- [ ] Branch names sanitized before use in file paths
- [ ] No secrets in state files or event logs

## 7. Packaging & Distribution

### npm package
```bash
npm publish                     # npm registry
clawhub package publish chrisufo/railclaw-pipeline   # ClawHub
```

### Install
```bash
openclaw plugins install chrisufo/railclaw-pipeline
```

One command. `postinstall.sh` runs `pip install -e ./python`.

### Config (in consumer's openclaw.json)
```json
{
  "plugins": {
    "entries": {
      "railclaw-pipeline": {
        "enabled": true,
        "config": {
          "repoPath": "/path/to/target/repo",
          "factoryPath": "/path/to/factory",
          "agents": { ... },
          "escalation": { ... }
        }
      }
    }
  }
}
```

## 8. Final Verification Checklist

- [ ] `npm run tsc` passes with all plugin files
- [ ] Node-side plugin tests pass
- [ ] Python test suite passes on Python 3.11+
- [ ] End-to-end pipeline run through plugin tool
- [ ] Resume after kill mid-stage
- [ ] Gemini clean detection (inline + body findings)
- [ ] Approval pause/resume via file protocol
- [ ] Deploy + health check on Pi ARM64
- [ ] Lessons learned + checkpoint archival
- [ ] `openclaw plugins install chrisufo/railclaw-pipeline` works end-to-end
- [ ] Plugin config sufficient — no hardcoded paths

## 9. Non-Goals

- [ ] No standalone CLI-only distribution (plugin is the primary interface)
- [ ] No orchestration logic in TypeScript (Python owns the pipeline)
- [ ] No slash commands until core tool path is stable
- [ ] No memory-only state for resumability
