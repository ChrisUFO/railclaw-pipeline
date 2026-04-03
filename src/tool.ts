import { Type, Static } from "@sinclair/typebox";
import { spawnPythonBridge } from "./python-bridge.js";
import type { PluginConfig } from "./config.js";
import { buildRuntimeConfig } from "./config.js";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

export const PipelineRunParameters = Type.Object({
  repoPath: Type.String({
    description: "Absolute path to the target git repo. Plugin resolves .pipeline.json from here.",
  }),
  action: Type.Union([
    Type.Literal("run"),
    Type.Literal("status"),
    Type.Literal("resume"),
    Type.Literal("abort"),
  ]),
  issueNumber: Type.Optional(Type.Number()),
  milestone: Type.Optional(Type.String()),
  hotfix: Type.Optional(Type.Boolean()),
  forceStage: Type.Optional(Type.String()),
  waitForCompletion: Type.Optional(Type.Boolean()),
});

type PipelineRunParams = Static<typeof PipelineRunParameters>;

export function registerPipelineTool(api: OpenClawPluginApi, config: PluginConfig) {
  api.registerTool({
    name: "pipeline_run",
    label: "Pipeline Run",
    description: "Run the coding factory pipeline for an issue or milestone",
    parameters: PipelineRunParameters,
    async execute(_id: string, params: PipelineRunParams) {
      if (params.action === "run" && !params.issueNumber && !params.milestone) {
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify({
                ok: false,
                error: "issueNumber or milestone is required for run action",
              }),
            },
          ],
          details: {},
        };
      }

      // Build per-call config: merge .pipeline.json over plugin defaults
      const runtimeConfig = buildRuntimeConfig(config, params.repoPath);

      const result = await spawnPythonBridge(runtimeConfig, params);

      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result),
          },
        ],
        details: {},
      };
    },
  });
}
