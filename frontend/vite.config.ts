import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` serves the UI on :5173 and proxies /api to the console API on :8080.
// Prod: `npm run build` emits frontend/dist, which `pipeline-agent console` serves itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          fluent: ["@fluentui/react-components"],
          charts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"],
        },
      },
    },
  },
});
