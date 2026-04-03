import { pipelineStore } from "./store.js";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

export function registerLifecycleHooks(api: OpenClawPluginApi): void {
  api.on("gateway_start", async () => {
    try {
      const map = pipelineStore.tryGetRuntime();
      if (map) {
        for (const key of Object.keys(map)) {
          const meta = map[key];
          if (meta && meta.status === "running") {
            map[key] = {
              ...meta,
              status: "interrupted",
              updatedAt: new Date().toISOString(),
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
