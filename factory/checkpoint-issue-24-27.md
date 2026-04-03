# Checkpoint: Issues #24 & #27 — Cycle 2 Bugs + Enhanced Logging

**Branch:** `fix/issue-24-27-cycle2-bugs-and-logging`
**Agent:** Blueprint
**Date:** 2026-04-03

---

## 1. Changes Needed

### File 1: `cycle2_gemini.py` — Duplicate `_get_agent_config()` + `--timeout` bug

**Location:** Lines 273–305 (bottom of file, `_get_agent_config` function)

**Bug:** Duplicate `_get_agent_config()` still passes `--timeout` to opencode in args_template (line ~282). The version in `pipeline.py` (lines 235–261) was already fixed and omits `--timeout`.

**Fix: Delete the entire duplicate function (lines 273–305).** The callers in this file (`_run_wrench_fix` at line 118, `_run_wrench_sr_fix` at line 147) import `AgentConfig` from `runner.agent` and can use the fixed `pipeline.py._get_agent_config`. However, since `_get_agent_config` is a private function in `pipeline.py`, the cleanest approach is:

- **Option A (preferred):** Import `_get_agent_config` from `pipeline.py` — but it's private, so instead extract it into a shared module (e.g., `runner/agent_config.py`) or just duplicate the *fixed* version here.
- **Option B (simplest):** Replace the duplicate with the fixed version (copy from `pipeline.py` lines 235–261) — removes `--timeout` from args_template on lines 282 and 289.

**Concrete diff:**
```python
# DELETE lines 273-305 (the whole _get_agent_config function)
# The callers already do: wrench_config = _get_agent_config(config, "wrench")
# So either import the fixed one or copy-paste the fixed version.
```

**Actual fix — replace the body of `_get_agent_config` in `cycle2_gemini.py`** to match `pipeline.py`'s fixed version (no `--timeout` in args_template):

- Line 282: `"run", "--dir", str(workdir), "--timeout", str(timeout), "{prompt}"` → `"run", "--dir", str(workdir), "{prompt}"`
- Line 289: Same change for the blueprint/wrench/scope/beaker/quill branch

---

### File 2: `review.py` — `has_formal_review` doesn't include COMMENTED

**Location:** `poll_reviews()`, line ~189

**Current code:**
```python
has_formal = any(r.get("state") in ("APPROVED", "CHANGES_REQUESTED") for r in reviews)
```

**Bug:** Gemini always posts COMMENTED reviews. `has_formal_review` is `False`, so the clean exit condition in `cycle2_gemini.py` (`is_clean and has_formal_review`) never triggers.

**Fix:** Add `"COMMENTED"` to the tuple:
```python
has_formal = any(r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") for r in reviews)
```

**Note:** `extract_findings_from_reviews()` (line ~152) already handles COMMENTED correctly — it extracts `<details>` blocks and falls back to creating a finding from the body. The issue is purely in `has_formal_review`.

---

### File 3: `cycle2_gemini.py` — `_extract_gemini_findings()` returns empty for COMMENTED without `<details>`

**Location:** `_extract_gemini_findings()`, lines 184–217

**Bug:** When Gemini posts COMMENTED with no `<details>` blocks, the function only creates findings for `CHANGES_REQUESTED` (line 214). For COMMENTED, findings=[], but `is_clean=False` (from `poll_reviews`). This creates the contradiction: findings=[] but review isn't clean.

**Fix:** Change the fallback condition on line 214 from:
```python
if not blocks and f.get("state") == "CHANGES_REQUESTED":
```
to:
```python
if not blocks and f.get("state") in ("CHANGES_REQUESTED", "COMMENTED"):
```

**BUT WAIT** — this would create findings from every Gemini COMMENTED body, including "LGTM" comments. Better approach: **keep `_extract_gemini_findings` as-is**, and fix the real problem in `poll_reviews` + the loop logic. The loop in `pipeline.py` checks `state.findings["current"]` count for stall detection. If COMMENTED with no findings means "looks good", then `is_clean` should be `True`.

**Revised Fix for Bug 2:** The root cause is in `extract_findings_from_reviews()` (review.py line ~166):
```python
if not details_findings and state in ("CHANGES_REQUESTED", "COMMENTED"):
    severity, category = classify_finding(body)
    findings.append(...)
```

This creates a finding from ANY COMMENTED review body. If Gemini posts "The code looks good" as COMMENTED, this creates a finding, making `is_clean=False`. This is wrong — a COMMENTED review without `<details>` blocks should be treated as informational, not a finding.

**Fix:** Remove `"COMMENTED"` from the fallback line in `extract_findings_from_reviews()`:
```python
# Line ~166 in review.py:
if not details_findings and state == "CHANGES_REQUESTED":
```

This way:
- COMMENTED with `<details>` blocks → findings extracted (correct, Gemini found issues)
- COMMENTED without `<details>` → no findings, `is_clean=True` (correct, informational)
- CHANGES_REQUESTED without `<details>` → finding from body (correct, substantive objection)

Combined with Fix #2 (COMMENTED in `has_formal_review`), the clean exit path now works:
1. Gemini posts COMMENTED with no `<details>` → `findings=[]`, `is_clean=True`, `has_formal_review=True`
2. Loop hits `is_clean and has_formal_review` → `gemini_clean=True` → exit

---

### File 4: `emitter.py` — Log rotation

**Location:** `EventEmitter.__init__()` and `flush_now()`

**Changes:**
1. Add `RotatingFileHandler`-style rotation to `flush_now()`:
   - After flush, check file size. If > 10MB, rotate: rename to `events.jsonl.1`, `.1` → `.2`, `.2` → `.3`, delete `.3`.
2. Add `run_events_path` property/method for per-run log files.
3. Add agent stdout/stderr capture in `emit()` — accept optional `stdout`/`stderr` kwargs, write to per-run log.

**Concrete implementation:**
```python
import shutil

MAX_EVENT_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ROTATED_FILES = 3

def _rotate_events(self) -> None:
    """Rotate events.jsonl at 10MB, keep 3 archives."""
    if not self.events_path.exists():
        return
    if self.events_path.stat().st_size < MAX_EVENT_FILE_SIZE:
        return
    # Shift existing archives
    for i in range(MAX_ROTATED_FILES, 0, -1):
        src = self.events_path.with_suffix(f".jsonl.{i}")
        if src.exists():
            if i == MAX_ROTATED_FILES:
                src.unlink()  # Delete oldest
            else:
                dst = self.events_path.with_suffix(f".jsonl.{i+1}")
                src.rename(dst)
    self.events_path.rename(self.events_path.with_suffix(".jsonl.1"))
```

Call `_rotate_events()` at the end of `flush_now()`.

**Per-run log files:** Add `run_dir` parameter to `__init__`:
```python
def __init__(self, events_path: Path, flush_interval: float = 30.0, run_dir: Path | None = None):
    self.run_dir = run_dir
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
```

**Stdout/stderr capture:** Add to `emit()`:
```python
def emit(self, event_type: str, stdout: str | None = None, stderr: str | None = None, **kwargs) -> None:
    # ... existing logic ...
    # Write agent output to per-run log
    if self.run_dir and (stdout or stderr):
        log_file = self.run_dir / f"{event_type}_{kwargs.get('agent', 'unknown')}.log"
        with open(log_file, "a") as f:
            if stdout:
                f.write(f"--- STDOUT {event['ts']} ---\n{stdout}\n")
            if stderr:
                f.write(f"--- STDERR {event['ts']} ---\n{stderr}\n")
```

---

### File 5: `pipeline.py` — Pass `run_dir` to EventEmitter + structured cycle2 logging

**Changes:**
1. In `run_pipeline()`, create per-run log directory:
```python
run_dir = config.factory_path / ".pipeline-events" / "runs" / f"issue-{state.issue_number}"
emitter = EventEmitter(events_path, run_dir=run_dir)
```
(Note: EventEmitter is created externally and passed in — update the caller in `cli.py` or wherever it's instantiated.)

2. Capture stdout/stderr in agent_start/agent_end calls. This requires `AgentRunner.run()` to return stdout/stderr (check `runner/agent.py` — it likely already does via `AgentResult`).

3. Add structured logging to the cycle2 while loop:
```python
# In the cycle2 while loop (pipeline.py ~line 190):
logger.info(
    "cycle2_round %d: findings=%d, gemini_clean=%s, stall=%d",
    state.cycle.cycle2_round, cur_count, state.cycle.gemini_clean, stall,
)
```

---

### File 6: New — `utils/cleanup.py` — 30-day cleanup + manual cleanup action

**New file:** `python/src/railclaw_pipeline/utils/cleanup.py`

```python
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

def cleanup_old_runs(base_dir: Path, max_age_days: int = 30) -> list[str]:
    """Delete run logs older than max_age_days. Returns list of deleted dirs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted = []
    if not base_dir.exists():
        return deleted
    for run_dir in base_dir.iterdir():
        if not run_dir.is_dir():
            continue
        mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            shutil.rmtree(run_dir)
            deleted.append(str(run_dir))
    return deleted
```

**Wire into pipeline CLI:** Add `cleanup` action handling that calls this function.

---

## 2. Order of Operations

### Phase 1: Bug fixes (Issue #24) — must be done first, no dependencies between them

1. **`review.py` line ~189** — Add `"COMMENTED"` to `has_formal_review` check (Bug 3 fix)
2. **`review.py` line ~166** — Remove `"COMMENTED"` from `extract_findings_from_reviews` fallback (Bug 2 fix)
3. **`cycle2_gemini.py` lines 282, 289** — Remove `--timeout` from args_template in duplicate `_get_agent_config` (Bug 1 fix)

### Phase 2: Logging enhancements (Issue #27) — depends on Phase 1 being committed

4. **`emitter.py`** — Add rotation + per-run log dir + stdout/stderr capture
5. **`pipeline.py`** — Wire `run_dir` into EventEmitter, add structured logging
6. **New: `utils/cleanup.py`** — Cleanup utility
7. **CLI entrypoint** — Wire `pipeline_run(action="cleanup")`

### Phase 3: Tests

8. Add/update tests for all changes

---

## 3. Testing Strategy

### Unit Tests — `review.py` fixes

**Test: `test_poll_reviews_commented_sets_formal_review`**
- Create a mock with a COMMENTED review, no findings
- Assert `result.has_formal_review == True` and `result.is_clean == True`

**Test: `test_poll_reviews_commented_with_details_has_findings`**
- Create a mock with a COMMENTED review containing `<details>` blocks
- Assert `result.is_clean == False`, `result.has_formal_review == True`, `len(result.findings) > 0`

**Test: `test_poll_reviews_changes_requested_without_details`**
- Create a mock with CHANGES_REQUESTED, no `<details>`
- Assert finding created from body (unchanged behavior)

### Unit Tests — `cycle2_gemini.py` fixes

**Test: `test_get_agent_config_no_timeout`**
- Call `_get_agent_config` for "wrench"
- Assert `"--timeout"` not in args_template

### Unit Tests — `emitter.py` rotation

**Test: `test_rotate_at_10mb`**
- Create EventEmitter, write >10MB of events, flush
- Assert `events.jsonl.1` exists, main file is empty/new

**Test: `test_max_3_rotated`**
- Create 4 rotations worth of data
- Assert only `.1`, `.2`, `.3` exist (no `.4`)

### Integration Test — Cycle 2 loop

**Test: `test_cycle2_exits_on_clean_commented`**
- Mock: Gemini posts COMMENTED, no `<details>`
- Assert loop exits with `gemini_clean=True` in 1 round

### Test file locations
- `python/tests/test_review.py` (modify)
- `python/tests/test_cycle2_gemini.py` (modify or create)
- `python/tests/test_emitter.py` (modify or create)
- `python/tests/test_cleanup.py` (new)

---

## 4. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Removing COMMENTED from fallback in `extract_findings_from_reviews` could miss real Gemini findings that don't use `<details>` | Medium | Gemini should always use `<details>` for structured findings. If Gemini posts unstructured CHANGES_REQUESTED, it's still caught. COMMENTED without details is informational by nature. Monitor first real run. |
| Log rotation could lose data during rotation race | Low | Rotation only happens in `flush_now()` which holds `_lock`. Single-threaded flush, no concurrent writers expected. |
| Per-run log files could accumulate disk usage | Low | 30-day cleanup handles this. Add a check in preflight stage to warn if >500MB of logs. |
| `_get_agent_config` duplication between files | Low | Long-term should extract to shared module. For now, keep both files' versions aligned. Add a comment noting the duplication. |
| Changing `has_formal_review` to include COMMENTED could affect cycle 1 behavior | Low | `has_formal_review` is only checked in cycle 2 code path (`run_gemini_loop`). Cycle 1 uses `scope_verdict`. |

---

## Summary of All Edits

| # | File | Line(s) | Change |
|---|------|---------|--------|
| 1 | `review.py` | ~189 | Add `"COMMENTED"` to `has_formal_review` tuple |
| 2 | `review.py` | ~166 | Remove `"COMMENTED"` from `extract_findings_from_reviews` fallback |
| 3 | `cycle2_gemini.py` | ~282 | Remove `--timeout` from first args_template |
| 4 | `cycle2_gemini.py` | ~289 | Remove `--timeout` from second args_template |
| 5 | `emitter.py` | new method | Add `_rotate_events()` method |
| 6 | `emitter.py` | `flush_now()` | Call `_rotate_events()` after flush |
| 7 | `emitter.py` | `__init__()` | Add `run_dir` parameter |
| 8 | `emitter.py` | `emit()` | Add `stdout`/`stderr` capture to per-run log |
| 9 | `pipeline.py` | `run_pipeline()` | Add structured cycle2 logging |
| 10 | pipeline CLI | entrypoint | Create `run_dir`, pass to EventEmitter, wire cleanup action |
| 11 | NEW `utils/cleanup.py` | — | `cleanup_old_runs()` function |
| 12 | NEW `python/tests/test_cleanup.py` | — | Tests for cleanup utility |
