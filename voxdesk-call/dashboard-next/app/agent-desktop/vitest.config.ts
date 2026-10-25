import path from "node:path";
import { defineConfig } from "vitest/config";

const dashboardRoot = path.resolve(__dirname, "../..");

export default defineConfig({
  root: dashboardRoot,
  resolve: { alias: { "@": dashboardRoot } },
  esbuild: { jsx: "automatic", jsxImportSource: "react" },
  test: {
    environment: "jsdom",
    include: [
      "app/agent-desktop/**/*.test.ts",
      "app/agent-desktop/**/*.test.tsx",
    ],
  },
});
