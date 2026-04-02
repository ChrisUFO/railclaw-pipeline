import { pipelineStore } from "./store.js";

export function registerLifecycleHooks(api: {
  on: (event: string, handler: () => void | Promise<void>) => void;
}): void {
  api.on("startup", async () => {
    try {
      const keys = pipelineStore.keys();
      for (const key of keys) {
        const meta = pipelineStore.get(key);
        if (meta && meta.status === "running") {
          pipelineStore.set(key, {
            ...meta,
            status: "interrupted",
            updatedAt: new Date().toISOString(),
          });
        }
      }
    } catch {
      // Store cleanup is advisory
    }
  });

  api.on("shutdown", async () => {
    try {
      pipelineStore.clear();
    } catch {
      // Non-critical
    }
  });
}
