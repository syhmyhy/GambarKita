/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// In development the React app and the API share one origin: Vite proxies API,
// media and WebSocket traffic to FastAPI. Relative URLs from the API then work
// unchanged and the browser never needs CORS.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000";
  // xfwd adds X-Forwarded-For so the API's per-IP limits see each phone on the
  // LAN (uvicorn trusts forwarded headers from 127.0.0.1 only, and uses the
  // right-most untrusted hop, so this is not spoofable).
  const proxy = { target, changeOrigin: false, xfwd: true };

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": proxy,
        "/media": proxy,
        "/ws": { ...proxy, ws: true },
      },
    },
    preview: {
      port: 4173,
      proxy: {
        "/api": proxy,
        "/media": proxy,
        "/ws": { ...proxy, ws: true },
      },
    },
    build: {
      target: "es2022",
      sourcemap: false,
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
      restoreMocks: true,
    },
  };
});
