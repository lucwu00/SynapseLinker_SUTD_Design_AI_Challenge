import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://localhost:8000";
const paths = ["/patients", "/predict", "/flow", "/stations", "/navigation", "/robot", "/robots",
               "/dashboard", "/events", "/simulation", "/demo", "/health"];

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: Object.fromEntries(paths.map((p) => [p, { target: backend, changeOrigin: true }])) },
});
