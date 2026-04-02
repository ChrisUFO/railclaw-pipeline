import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  resolve: {
    alias: {
      "openclaw/plugin-sdk/plugin-entry": path.resolve(__dirname, "tests/__mocks__/plugin-entry.ts"),
      "openclaw/plugin-sdk/runtime-store": path.resolve(__dirname, "tests/__mocks__/runtime-store.ts"),
    },
  },
  test: {
    include: ["tests/**/*.test.ts"],
  },
});
