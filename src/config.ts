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
  // Internal: directory where the .pipeline.json was found. Used to resolve
  // relative paths (eg. factoryPath) relative to the config location.
  __configDir?: string;
}

const DEFAULT_CONFIG: PluginConfig = {
  repoPath: "",
  factoryPath: "factory",
  pythonCommand: "railclaw-pipeline",
  stateDir: ".pipeline-state",
  eventsDir: ".pipeline-events",
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

  // Walk up the directory tree but stop at the git repo root (dir containing .git)
  while (dir !== root) {
    const candidate = path.join(dir, ".pipeline.json");
    try {
      if (fs.existsSync(candidate)) {
        // Read and validate the config
        const rawRaw = fs.readFileSync(candidate, "utf-8");
        const raw = JSON.parse(rawRaw) as RepoPipelineConfig;
        // Basic runtime validation of the shape
        // Allow omission of fields; validate types when present
        if (raw && typeof raw === "object") {
          // Shallow structural validation
          if (raw.factoryPath !== undefined && typeof raw.factoryPath !== "string") {
            throw new Error("Invalid type for factoryPath in .pipeline.json");
          }
          if (raw.agents !== undefined && typeof raw.agents !== "object") {
            throw new Error("Invalid type for agents in .pipeline.json");
          }
          if (raw.timing !== undefined && typeof raw.timing !== "object") {
            throw new Error("Invalid type for timing in .pipeline.json");
          }
          if (raw.pm2 !== undefined && typeof raw.pm2 !== "object") {
            throw new Error("Invalid type for pm2 in .pipeline.json");
          }
          if (raw.escalation !== undefined && typeof raw.escalation !== "object") {
            throw new Error("Invalid type for escalation in .pipeline.json");
          }
        }
        // Attach configDir so downstream can resolve relative paths from this directory
        (raw as RepoPipelineConfig).__configDir = dir;
        return raw as RepoPipelineConfig;
      }
    } catch (err) {
      // Re-throw validation errors directly (they have their own messages)
      if (err instanceof Error && err.message.startsWith("Invalid type for")) {
        throw err;
      }
      // File exists but is malformed (parse error) — log and decide whether to fail at repo root
      // If we are at the repo root (dir contains a .git), escalate for visibility
      const candidatePath = path.join(dir, ".pipeline.json");
      const gitDir = path.join(dir, ".git");
      if (fs.existsSync(gitDir) && fs.statSync(gitDir).isDirectory()) {
        // Malformed at repo root: rethrow to fail loudly
        console.error(`Malformed or unreadable .pipeline.json at repo root: ${candidatePath}`);
        throw new Error(`Malformed .pipeline.json at repo root: ${candidatePath}`);
      } else {
        console.warn(`Malformed or unreadable .pipeline.json at ${candidatePath}, continuing walk`);
      }
    }
    // Before ascending, check if current dir is a git root.
    // If we're about to leave the git repo without finding .pipeline.json, stop.
    const currentGit = path.join(dir, ".git");
    if (fs.existsSync(currentGit) && fs.statSync(currentGit).isDirectory()) {
      // We're at a git root with no .pipeline.json — stop traversal
      break;
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
export function buildRuntimeConfig(pluginConfig: PluginConfig, repoPath: string): PluginConfig {
  const repoConfig = resolveRepoPipelineConfig(repoPath);
  // Determine base directory for relative resolutions
  const configDir = (repoConfig as any)?.__configDir ?? repoPath;

  // If repoConfig specifies a factoryPath, resolve it relative to the location of the config
  let factoryPath = pluginConfig.factoryPath;
  if (repoConfig?.factoryPath) {
    factoryPath = path.isAbsolute(repoConfig.factoryPath)
      ? repoConfig.factoryPath
      : path.resolve(configDir, repoConfig.factoryPath);
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
