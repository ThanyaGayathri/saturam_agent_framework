import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { prototypeAuthDevPlugin } from "./vite-plugin-prototype-auth-dev.js";

export default defineConfig({
  plugins: [react(), prototypeAuthDevPlugin()],
  server: {
    port: 3000,
    // Auto-opening a browser throws/hangs in headless contexts (CI, SSH, containers,
    // the Playwright-driven check this repo's README describes) - skip it there.
    open: !process.env.CI,
  },
});
