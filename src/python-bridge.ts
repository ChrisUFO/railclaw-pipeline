import { spawn } from "child_process";
import type { PluginConfig, PipelineRunParams } from "./config.js";
import { pipelineStore } from "./store.js";

export interface PythonBridgeResult {
  ok: boolean;
  action: string;
  stage?: string;
  status?: string;
  issueNumber?: number;
  prNumber?: number;
  branch?: string;
  message?: string;
  statePath?: string;
  pid?: number;
  error?: string | null;
}

export function spawnPythonBridge(
  config: PluginConfig,
  params: PipelineRunParams,
): Promise<PythonBridgeResult> {
  return new Promise((resolve) => {
    const args: string[] = [params.action];

    args.push("--repo-path", config.repoPath);
    args.push("--factory-path", config.factoryPath);

    if (params.issueNumber !== undefined) {
      args.push("--issue", params.issueNumber.toString());
    }
    if (params.milestone !== undefined) {
      args.push("--milestone", params.milestone);
    }
    if (params.hotfix !== undefined && params.hotfix) {
      args.push("--hotfix");
    }
    if (params.forceStage !== undefined) {
      args.push("--force-stage", params.forceStage);
    }

    const detach =
      (params.action === "run" || params.action === "resume") && params.detach !== false;
    if (detach) {
      args.push("--detach");
    }

    if (params.since !== undefined) {
      args.push("--since", params.since);
    }

    const proc = spawn(config.pythonCommand, args, {
      cwd: config.repoPath,
      env: {
        ...process.env,
        RAILCLAW_FACTORY_PATH: config.factoryPath,
        RAILCLAW_STATE_DIR: config.stateDir,
        RAILCLAW_EVENTS_DIR: config.eventsDir,
        RAILCLAW_REPO_PATH: config.repoPath,
      },
    });

    let stdout = "";
    let stderr = "";
    let resolved = false;

    proc.stdout.on("data", (data) => {
      stdout += data.toString();

      if (detach && !resolved) {
        const lines = stdout.split("\n");
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const parsed = JSON.parse(trimmed) as PythonBridgeResult;
            if (parsed.ok && parsed.status === "started") {
              resolved = true;
              updateStore(parsed);
              resolve(parsed);
              return;
            }
          } catch {
            // not a complete JSON line yet, keep accumulating
          }
        }
      }
    });

    proc.stderr.on("data", (data) => {
      stderr += data.toString();
    });

    proc.on("close", (code) => {
      if (resolved) return;

      if (code !== 0) {
        resolve({
          ok: false,
          action: params.action,
          error: `Python process exited with code ${code}: ${stderr}`,
        });
        return;
      }

      try {
        const result = JSON.parse(stdout.trim()) as PythonBridgeResult;
        updateStore(result);
        resolve(result);
      } catch (error) {
        resolve({
          ok: false,
          action: params.action,
          error: `Failed to parse Python output: ${error instanceof Error ? error.message : String(error)}`,
        });
      }
    });

    proc.on("error", (error) => {
      if (resolved) return;
      resolve({
        ok: false,
        action: params.action,
        error: `Failed to spawn Python process: ${error.message}`,
      });
    });
  });
}

function updateStore(result: PythonBridgeResult): void {
  if (result.ok && result.issueNumber && result.stage) {
    try {
      const map = pipelineStore.tryGetRuntime() ?? {};
      const key = `run:${result.issueNumber}`;
      const existing = map[key];
      map[key] = {
        issueNumber: result.issueNumber,
        stage: result.stage,
        status: result.status === "started" ? "running" : (result.status ?? "running"),
        startedAt: existing?.startedAt ?? new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        statePath: result.statePath ?? existing?.statePath ?? "",
        pid: result.pid ?? existing?.pid,
      };
      pipelineStore.setRuntime(map);
    } catch {
      // Store is advisory — failures are non-critical
    }
  }
}
