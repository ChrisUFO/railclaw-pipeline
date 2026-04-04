import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { registerPipelineTool } from "./tool.js";
import { normalizeConfig } from "./config.js";
import { registerLifecycleHooks } from "./hooks.js";

export default definePluginEntry({
  id: "railclaw-pipeline",
  name: "RailClaw Pipeline Orchestrator",
  description:
    "Automated coding factory pipeline: planning → implementation → review → merge → deploy → QA",
  register(api) {
    const config = normalizeConfig(api.pluginConfig ?? {});
    registerPipelineTool(api, config);
    registerLifecycleHooks(api, config);
  },
});
