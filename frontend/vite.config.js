import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { prototypeAuthDevPlugin } from "./vite-plugin-prototype-auth-dev.js";

export default defineConfig({
  plugins: [react(), prototypeAuthDevPlugin()],
  server: {
    port: 3000,
    open: true,
  },
});
