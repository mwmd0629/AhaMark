import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
export default defineConfig({
  plugins: [react()],
  resolve: {
    preserveSymlinks: true,
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: [resolve(process.cwd(), "vitest.setup.ts")],
  },
});
