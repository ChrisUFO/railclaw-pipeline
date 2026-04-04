declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface OpenClawPluginApi {
    pluginConfig: Record<string, unknown>;
    registerTool(tool: {
      name: string;
      label: string;
      description: string;
      parameters: import("@sinclair/typebox").TObject;
      execute(
        id: string,
        params: Record<string, unknown>,
      ): Promise<{
        content: Array<{ type: string; text: string }>;
        details: Record<string, unknown>;
      }>;
    }): void;
    on(event: string, handler: (...args: unknown[]) => void | Promise<void>): void;
  }

  export interface PluginEntry {
    id: string;
    name: string;
    description: string;
    register(api: OpenClawPluginApi): void;
  }

  export function definePluginEntry(entry: PluginEntry): PluginEntry;
}

declare module "openclaw/plugin-sdk/runtime-store" {
  export interface PluginRuntimeStore<T> {
    tryGetRuntime(): T | undefined;
    setRuntime(map: T): void;
    getRuntime(): T;
    clearRuntime(): void;
  }

  export function createPluginRuntimeStore<T>(name: string): PluginRuntimeStore<T>;
}
