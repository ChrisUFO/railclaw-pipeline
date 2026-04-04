import { pipelineStore } from "./store.js";
import { spawn } from "child_process";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginConfig } from "./config.js";

function isProcessAlive(pythonCommand: string, pid: number): Promise<boolean> {
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

export function registerLifecycleHooks(api: OpenClawPluginApi, config: PluginConfig): void {
  api.on("gateway_start", async () => {
    try {
      const map = pipelineStore.tryGetRuntime();
      if (map) {
        for (const key of Object.keys(map)) {
          const meta = map[key];
          if (meta && meta.status === "running") {
            if (meta.pid) {
              const alive = await isProcessAlive(config.pythonCommand, meta.pid);
              if (!alive) {
                map[key] = {
                  ...meta,
                  status: "interrupted",
                  updatedAt: new Date().toISOString(),
                };
              }
            } else {
              map[key] = {
                ...meta,
                status: "interrupted",
                updatedAt: new Date().toISOString(),
              };
            }
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
