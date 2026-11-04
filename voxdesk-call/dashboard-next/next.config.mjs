import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `@voxdesk/web-sdk` is `file:../sdk/web`: a symlink that points outside this
  // project. Next refuses to compile a module whose real path escapes the
  // workspace root it inferred, so the root is declared explicitly. Both bundlers
  // also need transpilePackages because the package ships TypeScript sources.
  turbopack: {
    root: path.resolve(here, ".."),
  },
  outputFileTracingRoot: path.resolve(here, ".."),
  transpilePackages: ["@voxdesk/web-sdk"],
};

export default nextConfig;
