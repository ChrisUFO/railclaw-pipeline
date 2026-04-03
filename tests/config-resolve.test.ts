import { describe, it, expect, beforeEach, afterEach } from "vitest";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import {
  resolveRepoPipelineConfig,
  buildRuntimeConfig,
  normalizeConfig,
} from "../src/config.js";
import type { PluginConfig } from "../src/config.js";

describe("resolveRepoPipelineConfig", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rcp-test-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns null when no .pipeline.json exists", () => {
    // Create a git repo with no .pipeline.json
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    const result = resolveRepoPipelineConfig(repoDir);
    expect(result).toBeNull();
  });

  it("finds .pipeline.json in the given directory", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "../factory" }),
    );
    const result = resolveRepoPipelineConfig(repoDir);
    expect(result).not.toBeNull();
    expect(result!.factoryPath).toBe("../factory");
    expect(result!.__configDir).toBe(repoDir);
  });

  it("walks up from a subdirectory to find .pipeline.json", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.mkdirSync(path.join(repoDir, "src", "deep"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "./factory" }),
    );
    const result = resolveRepoPipelineConfig(path.join(repoDir, "src", "deep"));
    expect(result).not.toBeNull();
    expect(result!.factoryPath).toBe("./factory");
    expect(result!.__configDir).toBe(repoDir);
  });

  it("H1: stops at .git boundary and does not walk above", () => {
    // Create two repos: parent/repo and parent/.git (parent is a repo too)
    const parentDir = path.join(tmpDir, "parent");
    fs.mkdirSync(path.join(parentDir, ".git"), { recursive: true });
    // Put a .pipeline.json ABOVE the parent repo (in tmpDir) — should NOT be found
    fs.writeFileSync(
      path.join(tmpDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "should-not-find" }),
    );
    const repoDir = path.join(parentDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    // No .pipeline.json in repoDir or parentDir
    const result = resolveRepoPipelineConfig(repoDir);
    expect(result).toBeNull();
  });

  it("H2: throws on malformed .pipeline.json at repo root", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.writeFileSync(path.join(repoDir, ".pipeline.json"), "not json{{{");
    expect(() => resolveRepoPipelineConfig(repoDir)).toThrow("Malformed .pipeline.json");
  });

  it("H2: warns and continues on malformed .pipeline.json in subdirectory", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.mkdirSync(path.join(repoDir, "sub"), { recursive: true });
    fs.writeFileSync(path.join(repoDir, "sub", ".pipeline.json"), "bad json");
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "found" }),
    );
    // Should find the valid one at repo root, not crash on the sub one
    const result = resolveRepoPipelineConfig(path.join(repoDir, "sub"));
    expect(result).not.toBeNull();
    expect(result!.factoryPath).toBe("found");
  });

  it("M3: validates field types — throws on wrong factoryPath type", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: 123 }),
    );
    expect(() => resolveRepoPipelineConfig(repoDir)).toThrow("Invalid type for factoryPath");
  });

  it("M3: validates field types — throws on wrong agents type", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ agents: "string" }),
    );
    expect(() => resolveRepoPipelineConfig(repoDir)).toThrow("Invalid type for agents");
  });
});

describe("buildRuntimeConfig", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "rcp-build-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("M2: resolves factoryPath relative to .pipeline.json location, not repoPath", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.mkdirSync(path.join(repoDir, "sub", "nested"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "my-factory" }),
    );
    const pluginConfig = normalizeConfig({});
    const runtimeConfig = buildRuntimeConfig(
      pluginConfig,
      path.join(repoDir, "sub", "nested"),
    );
    // factoryPath should resolve relative to repoDir (where .pipeline.json is), not nested
    expect(runtimeConfig.factoryPath).toBe(path.join(repoDir, "my-factory"));
  });

  it("uses absolute factoryPath as-is", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    fs.writeFileSync(
      path.join(repoDir, ".pipeline.json"),
      JSON.stringify({ factoryPath: "/absolute/factory" }),
    );
    const pluginConfig = normalizeConfig({});
    const runtimeConfig = buildRuntimeConfig(pluginConfig, repoDir);
    expect(runtimeConfig.factoryPath).toBe("/absolute/factory");
  });

  it("falls back to plugin config factoryPath when repo has no .pipeline.json", () => {
    const repoDir = path.join(tmpDir, "repo");
    fs.mkdirSync(path.join(repoDir, ".git"), { recursive: true });
    const pluginConfig = normalizeConfig({ factoryPath: "/default/factory" });
    const runtimeConfig = buildRuntimeConfig(pluginConfig, repoDir);
    expect(runtimeConfig.factoryPath).toBe("/default/factory");
  });
});
