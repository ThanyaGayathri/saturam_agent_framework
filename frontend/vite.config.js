import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { prototypeAuthDevPlugin } from "./vite-plugin-prototype-auth-dev.js";

export default defineConfig({
  plugins: [react(), prototypeAuthDevPlugin()],
  server: {
    port: 3000,
    // Fail fast instead of silently shifting to another port - a silent shift would break
    // the backend's CORS allow-list default and invalidate the port docs this PR added.
    strictPort: true,
    // Auto-opening a browser throws/hangs in headless contexts (CI, SSH, containers,
    // the Playwright-driven check this repo's README describes) - skip it there.
    open: !process.env.CI,
  },
});
