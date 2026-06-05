import { defineConfig } from "vite";
import { resolve } from "node:path";

// Build the dashboard to ONE self-contained IIFE file, vendored at
// config/sandbox-web.js, which `sb` inlines into the served page. No chunks,
// no async imports, no CSS file (Tailwind is built separately + inlined).
//
// Dev (`npm run dev`): a Vite HMR server that proxies /api to the running
// `./sb web` Python backend, so you iterate on the UI against real data.
export default defineConfig({
  root: import.meta.dirname,
  server: {
    port: 5199,
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
  build: {
    target: "es2018",
    outDir: resolve(import.meta.dirname, "../../config"),
    emptyOutDir: false, // share the dir with sandbox-web.css — never wipe it
    lib: {
      entry: resolve(import.meta.dirname, "src/main.ts"),
      name: "SandboxWeb",
      formats: ["iife"],
      fileName: () => "sandbox-web.js",
    },
    rollupOptions: {
      output: { inlineDynamicImports: true, extend: true },
    },
    cssCodeSplit: false,
    minify: "esbuild",
  },
});
