import { vi, describe, it, expect, beforeEach } from "vitest";
import * as childProcess from "child_process";

vi.mock("openclaw/plugin-sdk/runtime-store", () => ({
  createPluginRuntimeStore: () => {
    const store = new Map<string, any>();
    return {
      get: (key: string) => store.get(key),
      set: (key: string, value: any) => {
        store.set(key, value);
      },
      keys: () => Array.from(store.keys()),
      clear: () => store.clear(),
      tryGetRuntime: () => {
        if (store.size === 0) return undefined;
        const obj: Record<string, any> = {};
        store.forEach((v, k) => {
          obj[k] = v;
        });
        return obj;
      },
      setRuntime: (value: Record<string, any>) => {
        store.clear();
        for (const [k, v] of Object.entries(value)) {
          store.set(k, v);
        }
      },
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
      stdout: {
        on: (_evt: string, cb: (d: Buffer) => void) => {
          cb(Buffer.from(""));
        },
      },
      stderr: {
        on: (_evt: string, cb: (d: Buffer) => void) => {
          cb(Buffer.from("error msg"));
        },
      },
      on: (event: string, cb: (code: number) => void) => {
        if (event === "close") cb(1);
      },
    }));

    const result = await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
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
      stdout: {
        on: (_evt: string, cb: (d: Buffer) => void) => {
          cb(Buffer.from(validJson));
        },
      },
      stderr: { on: () => {} },
      on: (event: string, cb: (code: number) => void) => {
        if (event === "close") cb(0);
      },
    }));

    const result = await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
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
      {
        pythonCommand: "bad-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
      { action: "status" } as any,
    );
    expect(result.ok).toBe(false);
    expect(result.error).toContain("Failed to spawn");
  });

  it("resolves immediately on started response in detach mode", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    const startedJson = JSON.stringify({
      ok: true,
      action: "run",
      stage: "stage0_preflight",
      status: "started",
      issueNumber: 42,
      statePath: "/tmp/state.json",
      pid: 12345,
    });

    (spawn as any).mockImplementation(() => ({
      stdout: {
        on: (_evt: string, cb: (d: Buffer) => void) => {
          cb(Buffer.from(startedJson + "\n"));
        },
      },
      stderr: { on: () => {} },
      on: () => {},
    }));

    const result = await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
      { action: "run", issueNumber: 42 } as any,
    );
    expect(result.ok).toBe(true);
    expect(result.status).toBe("started");
    expect(result.pid).toBe(12345);
  });

  it("waits for close when detach is false", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    const validJson = JSON.stringify({
      ok: true,
      action: "run",
      stage: "stage0_preflight",
      status: "completed",
      issueNumber: 42,
      statePath: "/tmp/state.json",
    });

    (spawn as any).mockImplementation(() => ({
      stdout: {
        on: (_evt: string, cb: (d: Buffer) => void) => {
          cb(Buffer.from(validJson));
        },
      },
      stderr: { on: () => {} },
      on: (event: string, cb: (code: number) => void) => {
        if (event === "close") cb(0);
      },
    }));

    const result = await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
      { action: "run", issueNumber: 42, detach: false } as any,
    );
    expect(result.ok).toBe(true);
    expect(result.status).toBe("completed");
  });

  it("passes --detach flag to args for run action", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    const startedJson = JSON.stringify({
      ok: true,
      action: "run",
      status: "started",
      issueNumber: 42,
      stage: "stage0_preflight",
      pid: 999,
    });

    (spawn as any).mockImplementation((_cmd: string, args: string[]) => {
      expect(args).toContain("--detach");
      return {
        stdout: {
          on: (_evt: string, cb: (d: Buffer) => void) => {
            cb(Buffer.from(startedJson + "\n"));
          },
        },
        stderr: { on: () => {} },
        on: () => {},
      };
    });

    await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
      { action: "run", issueNumber: 42 } as any,
    );
  });

  it("does not pass --detach for status action", async () => {
    const { spawn } = await import("child_process");
    const { spawnPythonBridge } = await import("../src/python-bridge.js");

    (spawn as any).mockImplementation((_cmd: string, args: string[]) => {
      expect(args).not.toContain("--detach");
      return {
        stdout: {
          on: (_evt: string, cb: (d: Buffer) => void) => {
            cb(Buffer.from('{"ok":true,"action":"status"}'));
          },
        },
        stderr: { on: () => {} },
        on: (event: string, cb: (code: number) => void) => {
          if (event === "close") cb(0);
        },
      };
    });

    await spawnPythonBridge(
      {
        pythonCommand: "test-cmd",
        repoPath: "/test",
        factoryPath: "/f",
        stateDir: ".s",
        eventsDir: ".e",
      } as any,
      { action: "status" } as any,
    );
  });
});
