import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The UI only ever talks to the local FastAPI backend, through this proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
