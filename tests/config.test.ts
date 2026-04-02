import { describe, it, expect } from 'vitest';
import { normalizeConfig } from "../src/config.js";

describe("normalizeConfig", () => {
  it("returns defaults for empty input", () => {
    const config = normalizeConfig({});
    expect(config.repoPath).toBeDefined();
    expect(config.factoryPath).toBeDefined();
    expect(config.pythonCommand).toBe("railclaw-pipeline");
    expect(config.stateDir).toBe(".pipeline-state");
    expect(config.eventsDir).toBe(".pipeline-events");
  });

  it("overrides provided string fields", () => {
    const config = normalizeConfig({
      repoPath: "/custom/repo",
      factoryPath: "/custom/factory",
      pythonCommand: "custom-cmd",
      stateDir: "custom-state",
      eventsDir: "custom-events",
    });
    expect(config.repoPath).toBe("/custom/repo");
    expect(config.factoryPath).toBe("/custom/factory");
    expect(config.pythonCommand).toBe("custom-cmd");
    expect(config.stateDir).toBe("custom-state");
    expect(config.eventsDir).toBe("custom-events");
  });

  it("merges agent config with defaults", () => {
    const config = normalizeConfig({
      agents: {
        blueprint: { model: "custom-model", timeout: 999 },
      },
    });
    expect(config.agents.blueprint.model).toBe("custom-model");
    expect(config.agents.blueprint.timeout).toBe(999);
    expect(config.agents.wrench.model).toBeDefined();
  });

  it("merges timing config with defaults", () => {
    const config = normalizeConfig({
      timing: { geminiPollInterval: 120 },
    });
    expect(config.timing.geminiPollInterval).toBe(120);
    expect(config.timing.approvalTimeout).toBe(86400);
  });

  it("merges escalation config with defaults", () => {
    const config = normalizeConfig({
      escalation: { wrenchSrAfterRound: 5 },
    });
    expect(config.escalation.wrenchSrAfterRound).toBe(5);
    expect(config.escalation.chrisAfterRound).toBe(5);
  });

  it("handles null nested objects", () => {
    const config = normalizeConfig({
      agents: null,
      timing: null,
    });
    expect(config.agents.blueprint).toBeDefined();
    expect(config.timing.geminiPollInterval).toBe(60);
  });
});
