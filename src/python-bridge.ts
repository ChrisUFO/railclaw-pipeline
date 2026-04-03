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
  error?: string | null;
}

export function spawnPythonBridge(
  config: PluginConfig,
  params: PipelineRunParams
): Promise<PythonBridgeResult> {
  return new Promise((resolve) => {
    const args: string[] = [params.action];

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

    proc.stdout.on("data", (data) => {
      stdout += data.toString();
    });

    proc.stderr.on("data", (data) => {
      stderr += data.toString();
    });

    proc.on("close", (code) => {
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

        if (result.ok && result.issueNumber && result.stage) {
          try {
            const map = pipelineStore.tryGetRuntime() ?? {};
            map[`run:${result.issueNumber}`] = {
              issueNumber: result.issueNumber,
              stage: result.stage,
              status: result.status ?? "running",
              startedAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              statePath: result.statePath ?? "",
            };
            pipelineStore.setRuntime(map);
          } catch {
            // Store is advisory — failures are non-critical
          }
        }

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
      resolve({
        ok: false,
        action: params.action,
        error: `Failed to spawn Python process: ${error.message}`,
      });
    });
  });
}
