import { createPluginRuntimeStore } from "openclaw/plugin-sdk/runtime-store";

export interface PipelineRunMetadata {
  issueNumber: number;
  stage: string;
  status: string;
  startedAt: string;
  updatedAt: string;
  statePath: string;
}

export const pipelineStore = createPluginRuntimeStore<PipelineRunMetadata>("railclaw-pipeline");
