import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type ProxyOptions } from "vite";

const API_TARGET = process.env.MANAGED_AGENTS_API_TARGET ?? "http://127.0.0.1:18080";

function apiProxy(): ProxyOptions {
  return {
    target: API_TARGET,
    changeOrigin: true,
    configure(proxy) {
      proxy.on("proxyReq", (request) => {
        request.removeHeader("origin");
        request.removeHeader("referer");
        request.removeHeader("authorization");
        request.removeHeader("x-top-account-id");
      });
    },
  };
}

export default defineConfig({
  root: resolve(__dirname, "managed-agents"),
  plugins: [react(), tailwindcss()],
  publicDir: resolve(__dirname, "src/managed-agents/public"),
  server: {
    port: 5174,
    proxy: { "/api": apiProxy(), "/v1": apiProxy() },
  },
  build: {
    outDir: resolve(__dirname, "dist/managed-agents"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "managed-agents/index.html"),
      output: {
        entryFileNames: "assets/app/[name]-[hash].js",
        chunkFileNames: "assets/chunks/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
