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
