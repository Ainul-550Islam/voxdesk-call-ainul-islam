// File: vitest.config.ts — Vitest configuration for enterprise UI tests and existing lib tests — updated to include new enterprise-ui test suite without breaking existing business logic
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // Route pages rely on Next's automatic JSX runtime and never import React;
  // vitest needs the same transform to render them.
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./"),
    },
  },
  test: {
    environment: "jsdom",
    include: [
      "lib/**/*.test.ts",
      "lib/**/*.test.tsx",
      "tests/**/*.test.tsx",
      "tests/**/*.test.ts",
      "components/**/*.test.tsx",
      // Route-level suites live beside the page they cover.
      "app/**/__tests__/**/*.test.tsx",
      "app/**/__tests__/**/*.test.ts",
    ],
  },
});
