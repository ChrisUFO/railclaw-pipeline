import { jest, describe, it, expect } from "@jest/globals";

jest.mock("openclaw/plugin-sdk/plugin-entry", () => ({
  definePluginEntry: (entry: any) => entry,
}));

jest.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: () => ({
    get: jest.fn(),
    set: jest.fn(),
    keys: jest.fn(() => []),
    clear: jest.fn(),
  }),
}));

const mockRegisterTool = jest.fn();
const mockOn = jest.fn();
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
