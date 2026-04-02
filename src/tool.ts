import { Type, Static } from "@sinclair/typebox";
import { spawnPythonBridge } from "./python-bridge.js";
import type { PluginConfig } from "./config.js";

const PipelineRunParameters = Type.Object({
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

export function registerPipelineTool(
  api: {
    registerTool: (tool: {
      name: string;
      description: string;
      parameters: typeof PipelineRunParameters;
      execute: (id: string, params: PipelineRunParams) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }) => void;
  },
  config: PluginConfig
) {
  api.registerTool({
    name: "pipeline_run",
    description: "Run the coding factory pipeline for an issue or milestone",
    parameters: PipelineRunParameters,
    async execute(_id: string, params: PipelineRunParams) {
      if (params.action === "run" && !params.issueNumber && !params.milestone) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                ok: false,
                error: "issueNumber or milestone is required for run action",
              }),
            },
          ],
        };
      }

      const result = await spawnPythonBridge(config, params);

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result),
          },
        ],
      };
    },
  });
}
