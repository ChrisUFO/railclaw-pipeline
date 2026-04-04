import { pipelineStore } from "./store.js";
import { spawn } from "child_process";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginConfig } from "./config.js";

function isProcessAlivePosix(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function isProcessAliveWindows(pythonCommand: string, pid: number): Promise<boolean> {
  return new Promise((resolve) => {
    const TIMEOUT_MS = 5000;
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill();
      resolve(false);
    }, TIMEOUT_MS);

    const proc = spawn(pythonCommand, ["_pid-check", "--pid", pid.toString()], {
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
    });
    let out = "";
    proc.stdout.on("data", (d: Buffer) => {
      out += d.toString();
    });
    proc.on("close", (code) => {
      if (timedOut) return;
      clearTimeout(timer);
      if (code === 0) {
        try {
          const parsed = JSON.parse(out.trim());
          resolve(parsed.alive === true);
        } catch {
          resolve(false);
        }
      } else {
        resolve(false);
      }
    });
    proc.on("error", () => {
      if (timedOut) return;
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function isProcessAlive(pythonCommand: string, pid: number): Promise<boolean> {
  if (process.platform !== "win32") {
    return Promise.resolve(isProcessAlivePosix(pid));
  }
  return isProcessAliveWindows(pythonCommand, pid);
}

export function registerLifecycleHooks(api: OpenClawPluginApi, config: PluginConfig): void {
  api.on("gateway_start", async () => {
    try {
      const map = pipelineStore.tryGetRuntime();
      if (map) {
        const keys = Object.keys(map);
        const checks = keys.map(async (key) => {
          const meta = map[key];
          if (!meta || meta.status !== "running") {
            return null;
          }
          if (!meta.pid) {
            return { key, status: "interrupted" };
          }
          const alive = await isProcessAlive(config.pythonCommand, meta.pid);
          return alive ? null : { key, status: "interrupted" };
        });

        const results = await Promise.all(checks);
        for (const result of results) {
          if (result) {
            map[result.key] = {
              issueNumber: map[result.key]!.issueNumber!,
              stage: map[result.key]!.stage!,
              status: result.status,
              startedAt: map[result.key]!.startedAt!,
              updatedAt: new Date().toISOString(),
              statePath: map[result.key]!.statePath ?? "",
              pid: map[result.key]!.pid,
            };
          }
        }
        pipelineStore.setRuntime(map);
      }
    } catch {
      // Store cleanup is advisory
    }
  });

  api.on("gateway_stop", async () => {
    try {
      pipelineStore.clearRuntime();
    } catch {
      // Non-critical
    }
  });
}
