import { jest, describe, it, expect } from "@jest/globals";

const VALID_PARAMS = {
  action: "run" as const,
  issueNumber: 42,
};

const VALID_STATUS_PARAMS = {
  action: "status" as const,
};

const mockApi = {
  registerTool: jest.fn(),
};

const mockConfig = {
  repoPath: "/test/repo",
  factoryPath: "/test/factory",
  pythonCommand: "railclaw-pipeline",
  stateDir: ".pipeline-state",
  eventsDir: ".pipeline-events",
  agents: {
    blueprint: { model: "test-model", timeout: 600 },
    wrench: { model: "test-model", timeout: 600 },
    scope: { model: "test-model", timeout: 600 },
    beaker: { model: "test-model", timeout: 600 },
    wrenchSr: { model: "test-model", timeout: 600 },
  },
  timing: { geminiPollInterval: 60, approvalTimeout: 86400, healthCheckTimeout: 30 },
  pm2: { processName: "test", ecosystemPath: "test.cjs" },
  escalation: { wrenchSrAfterRound: 3, chrisAfterRound: 5 },
};

jest.mock("child_process", () => ({
  spawn: jest.fn(() => ({
    stdout: { on: jest.fn() },
    stderr: { on: jest.fn() },
    on: jest.fn((event: string, cb: (code: number) => void) => {
      if (event === "close") {
        cb(0);
      }
    }),
  })),
}));

describe("tool schema", () => {
  it("registerPipelineTool registers with correct name", async () => {
    const { registerPipelineTool } = await import("../src/tool.js");
    registerPipelineTool(mockApi, mockConfig as any);
    expect(mockApi.registerTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "pipeline_run",
      }),
    );
  });

  it("tool has execute function", async () => {
    const { registerPipelineTool } = await import("../src/tool.js");
    registerPipelineTool(mockApi, mockConfig as any);
    const tool = mockApi.registerTool.mock.calls[0][0];
    expect(typeof tool.execute).toBe("function");
  });

  it("execute rejects run without issueNumber or milestone", async () => {
    const { registerPipelineTool } = await import("../src/tool.js");
    registerPipelineTool(mockApi, mockConfig as any);
    const tool = mockApi.registerTool.mock.calls[0][0];
    const result = await tool.execute("id-1", { action: "run" });
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.ok).toBe(false);
    expect(parsed.error).toContain("required");
  });
});
