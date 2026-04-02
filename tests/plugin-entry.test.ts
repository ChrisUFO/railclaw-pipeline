import { vi, describe, it, expect } from 'vitest';

vi.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (entry: any) => entry,
}));

vi.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: () => ({
    get: vi.fn(),
    set: vi.fn(),
    keys: vi.fn(() => []),
    clear: vi.fn(),
  }),
}));

const mockRegisterTool = vi.fn();
const mockOn = vi.fn();
const mockApi = {
  pluginConfig: {
    repoPath: "/test/repo",
    factoryPath: "/test/factory",
  },
  registerTool: mockRegisterTool,
  on: mockOn,
};

describe("plugin entry", () => {
  it("exports a default entry with register function", async () => {
    const entry = (await import("../src/index.js")).default;
    expect(entry).toBeDefined();
    expect(entry.id).toBe("railclaw-pipeline");
    expect(entry.name).toBe("RailClaw Pipeline Orchestrator");
    expect(typeof entry.register).toBe("function");
  });

  it("calls registerTool during register", async () => {
    const entry = (await import("../src/index.js")).default;
    entry.register(mockApi);
    expect(mockRegisterTool).toHaveBeenCalledTimes(1);
  });

  it("registers lifecycle hooks", async () => {
    const entry = (await import("../src/index.js")).default;
    entry.register(mockApi);
    expect(mockOn).toHaveBeenCalled();
  });
});
