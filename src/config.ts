import * as fs from "fs";
import * as path from "path";
import type { Static } from "@sinclair/typebox";
import type { PipelineRunParameters } from "./tool.js";

export interface PluginConfig {
  repoPath: string;
  factoryPath: string;
  pythonCommand: string;
  stateDir: string;
  eventsDir: string;
  agents: {
    blueprint: { model: string; timeout: number };
    wrench: { model: string; timeout: number };
    scope: { model: string; timeout: number };
    beaker: { model: string; timeout: number };
    wrenchSr: { model: string; timeout: number };
  };
  timing: {
    geminiPollInterval: number;
    approvalTimeout: number;
    healthCheckTimeout: number;
  };
  pm2: {
    processName: string;
    ecosystemPath: string;
  };
  escalation: {
    wrenchSrAfterRound: number;
    chrisAfterRound: number;
  };
}

export type PipelineRunParams = Static<typeof PipelineRunParameters>;

/** Shape of a `.pipeline.json` file found at a repo root. */
export interface RepoPipelineConfig {
  factoryPath?: string;
  agents?: PluginConfig["agents"];
  timing?: PluginConfig["timing"];
  pm2?: PluginConfig["pm2"];
  escalation?: PluginConfig["escalation"];
}

const DEFAULT_CONFIG: PluginConfig = {
  repoPath: "/home/chris/.openclaw/agents/railrunner/workspace/repos/RailClaw",
  factoryPath: "/home/chris/.openclaw/agents/railrunner/workspace/factory",
  pythonCommand:
    "/home/chris/.openclaw/agents/railrunner/workspace/repos/railclaw-pipeline/python/.venv/bin/railclaw-pipeline",
  stateDir: "/home/chris/.openclaw/agents/railrunner/workspace/factory/.pipeline-state",
  eventsDir: "/home/chris/.openclaw/agents/railrunner/workspace/factory/.pipeline-events",
  agents: {
    blueprint: { model: "openai/gpt-5.4", timeout: 600 },
    wrench: { model: "zai/glm-5-turbo", timeout: 1200 },
    scope: { model: "minimax/MiniMax-M2.7", timeout: 600 },
    beaker: { model: "openai/gpt-5.4-mini", timeout: 600 },
    wrenchSr: { model: "gemini/gemini-3.1-pro-preview", timeout: 1200 },
  },
  timing: {
    geminiPollInterval: 60,
    approvalTimeout: 86400,
    healthCheckTimeout: 30,
  },
  pm2: {
    processName: "railclaw-mc",
    ecosystemPath: "ecosystem.config.cjs",
  },
  escalation: {
    wrenchSrAfterRound: 3,
    chrisAfterRound: 5,
  },
};

/**
 * Walk up from `startDir` looking for a `.pipeline.json` file.
 * Returns the parsed config or null if not found (stops at filesystem root).
 */
export function resolveRepoPipelineConfig(startDir: string): RepoPipelineConfig | null {
  let dir = path.resolve(startDir);
  const root = path.parse(dir).root;

  while (dir !== root) {
    const candidate = path.join(dir, ".pipeline.json");
    try {
      if (fs.existsSync(candidate)) {
        const raw = JSON.parse(fs.readFileSync(candidate, "utf-8"));
        return raw as RepoPipelineConfig;
      }
    } catch {
      // File exists but is malformed — skip and keep walking up
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }

  return null;
}

/**
 * Merge per-repo `.pipeline.json` config over the plugin-level defaults.
 * `repoPath` overrides the plugin-level repoPath.
 */
export function buildRuntimeConfig(
  pluginConfig: PluginConfig,
  repoPath: string,
): PluginConfig {
  const repoConfig = resolveRepoPipelineConfig(repoPath);

  // If repoConfig specifies a factoryPath, resolve it relative to where the .pipeline.json was found
  let factoryPath = pluginConfig.factoryPath;
  if (repoConfig?.factoryPath) {
    factoryPath = path.isAbsolute(repoConfig.factoryPath)
      ? repoConfig.factoryPath
      : path.join(repoPath, repoConfig.factoryPath);
  }

  return {
    ...pluginConfig,
    repoPath: path.resolve(repoPath),
    factoryPath,
    agents: repoConfig?.agents
      ? { ...DEFAULT_CONFIG.agents, ...pluginConfig.agents, ...repoConfig.agents }
      : pluginConfig.agents,
    timing: repoConfig?.timing
      ? { ...DEFAULT_CONFIG.timing, ...pluginConfig.timing, ...repoConfig.timing }
      : pluginConfig.timing,
    pm2: repoConfig?.pm2
      ? { ...DEFAULT_CONFIG.pm2, ...pluginConfig.pm2, ...repoConfig.pm2 }
      : pluginConfig.pm2,
    escalation: repoConfig?.escalation
      ? { ...DEFAULT_CONFIG.escalation, ...pluginConfig.escalation, ...repoConfig.escalation }
      : pluginConfig.escalation,
  };
}

export function normalizeConfig(userConfig: Record<string, unknown>): PluginConfig {
  return {
    repoPath:
      typeof userConfig.repoPath === "string" ? userConfig.repoPath : DEFAULT_CONFIG.repoPath,
    factoryPath:
      typeof userConfig.factoryPath === "string"
        ? userConfig.factoryPath
        : DEFAULT_CONFIG.factoryPath,
    pythonCommand:
      typeof userConfig.pythonCommand === "string"
        ? userConfig.pythonCommand
        : DEFAULT_CONFIG.pythonCommand,
    stateDir:
      typeof userConfig.stateDir === "string" ? userConfig.stateDir : DEFAULT_CONFIG.stateDir,
    eventsDir:
      typeof userConfig.eventsDir === "string" ? userConfig.eventsDir : DEFAULT_CONFIG.eventsDir,
    agents: {
      ...DEFAULT_CONFIG.agents,
      ...(typeof userConfig.agents === "object" && userConfig.agents !== null
        ? userConfig.agents
        : {}),
    } as PluginConfig["agents"],
    timing: {
      ...DEFAULT_CONFIG.timing,
      ...(typeof userConfig.timing === "object" && userConfig.timing !== null
        ? userConfig.timing
        : {}),
    } as PluginConfig["timing"],
    pm2: {
      ...DEFAULT_CONFIG.pm2,
      ...(typeof userConfig.pm2 === "object" && userConfig.pm2 !== null ? userConfig.pm2 : {}),
    } as PluginConfig["pm2"],
    escalation: {
      ...DEFAULT_CONFIG.escalation,
      ...(typeof userConfig.escalation === "object" && userConfig.escalation !== null
        ? userConfig.escalation
        : {}),
    } as PluginConfig["escalation"],
  };
}
