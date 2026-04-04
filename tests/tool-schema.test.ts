import { vi, describe, it, expect } from "vitest";

const VALID_PARAMS = {
  action: "run" as const,
  issueNumber: 42,
};

const mockApi = {
  registerTool: vi.fn(),
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

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn((event: string, cb: (code: number) => void) => {
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

  it("schema includes detach parameter", async () => {
    const { PipelineRunParameters } = await import("../src/tool.js");
    const schema = PipelineRunParameters;
    const props = (schema as any).properties;
    expect(props).toHaveProperty("detach");
  });

  it("schema includes notifications action", async () => {
    const { PipelineRunParameters } = await import("../src/tool.js");
    const schema = PipelineRunParameters;
    const actionSchema = (schema as any).properties.action;
    const anyOf = actionSchema.anyOf || actionSchema.allOf;
    const literals = anyOf
      ? anyOf.map((s: any) => s.const)
      : actionSchema.const
        ? [actionSchema.const]
        : [];
    expect(literals).toContain("notifications");
  });

  it("schema includes since parameter", async () => {
    const { PipelineRunParameters } = await import("../src/tool.js");
    const schema = PipelineRunParameters;
    const props = (schema as any).properties;
    expect(props).toHaveProperty("since");
  });
});
