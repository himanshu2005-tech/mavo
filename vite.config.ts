import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/.build/**", "**/backend/**", "**/src-tauri/**"] },
    fs: { deny: [".env", ".env.*", "*.{crt,pem}", "**/.git/**", "**/.build/**", "**/backend/**"] }
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: { target: "chrome105", minify: "oxc", sourcemap: true }
});
