import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = fileURLToPath(new URL(".", import.meta.url));

// API lives on :8000 (FastAPI); the dev server proxies /api → backend so the browser never sees CORS.
export default defineConfig({
  root,
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.WFO_API ?? process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 4500 },
});
