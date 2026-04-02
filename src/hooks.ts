export function registerLifecycleHooks(api: {
  on: (event: string, handler: () => void | Promise<void>) => void;
}): void {
  api.on("shutdown", async () => {
    console.log("[railclaw-pipeline] Plugin shutting down");
  });
}
