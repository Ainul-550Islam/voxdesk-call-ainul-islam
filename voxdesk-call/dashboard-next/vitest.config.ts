// File: vitest.config.ts — Vitest configuration for enterprise UI tests and existing lib tests — updated to include new enterprise-ui test suite without breaking existing business logic
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./"),
    },
  },
  test: {
    environment: "jsdom",
    include: ["lib/**/*.test.ts", "lib/**/*.test.tsx", "tests/**/*.test.tsx", "tests/**/*.test.ts", "components/**/*.test.tsx"],
  },
});
