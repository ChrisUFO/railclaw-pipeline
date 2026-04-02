import { vi, describe, it, expect, beforeEach } from 'vitest';
import * as childProcess from "child_process";

vi.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: () => {
    const store = new Map<string, any>();
    return {
      get: (key: string) => store.get(key),
      set: (key: string, value: any) => { store.set(key, value); },
      keys: () => Array.from(store.keys()),
      clear: () => store.clear(),
    };
  },
}));

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    on: () => {},
  })),
}));

describe("python-bridge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("spawnPythonBridge returns error for non-zero exit", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    (spawn as any).mockImplementation(() => ({
      stdout: { on: (_evt: string, cb: (d: Buffer) => void) => { cb(Buffer.from("")); } },
      stderr: { on: (_evt: string, cb: (d: Buffer) => void) => { cb(Buffer.from("error msg")); } },
      on: (event: string, cb: (code: number) => void) => {
        if (event === "close") cb(1);
      },
    }));

    const result = await spawnPythonBridge(
      { pythonCommand: "test-cmd", repoPath: "/test", factoryPath: "/f", stateDir: ".s", eventsDir: ".e" } as any,
      { action: "status" } as any,
    );
    expect(result.ok).toBe(false);
    expect(result.error).toContain("exited with code 1");
  });

  it("spawnPythonBridge parses valid JSON output", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    const validJson = JSON.stringify({
      ok: true,
      action: "run",
      stage: "stage0_preflight",
      status: "running",
      issueNumber: 42,
      statePath: "/tmp/state.json",
    });

    (spawn as any).mockImplementation(() => ({
      stdout: { on: (_evt: string, cb: (d: Buffer) => void) => { cb(Buffer.from(validJson)); } },
      stderr: { on: () => {} },
      on: (event: string, cb: (code: number) => void) => {
        if (event === "close") cb(0);
      },
    }));

    const result = await spawnPythonBridge(
      { pythonCommand: "test-cmd", repoPath: "/test", factoryPath: "/f", stateDir: ".s", eventsDir: ".e" } as any,
      { action: "run", issueNumber: 42 } as any,
    );
    expect(result.ok).toBe(true);
    expect(result.stage).toBe("stage0_preflight");
    expect(result.issueNumber).toBe(42);
  });

  it("spawnPythonBridge handles spawn error", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    (spawn as any).mockImplementation(() => ({
      stdout: { on: () => {} },
      stderr: { on: () => {} },
      on: (event: string, cb: (err: Error) => void) => {
        if (event === "error") cb(new Error("spawn failed"));
      },
    }));

    const result = await spawnPythonBridge(
      { pythonCommand: "bad-cmd", repoPath: "/test", factoryPath: "/f", stateDir: ".s", eventsDir: ".e" } as any,
      { action: "status" } as any,
    );
    expect(result.ok).toBe(false);
    expect(result.error).toContain("Failed to spawn");
  });
});
