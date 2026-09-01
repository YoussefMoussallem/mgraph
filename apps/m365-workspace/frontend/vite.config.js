import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react(), tailwindcss()],
    test: {
      // jsdom gives us window/document for component tests; node mode
      // would require every test that touches the DOM to opt in.
      environment: "jsdom",
      globals: true,
      setupFiles: ["tests/setup.js"],
      include: ["tests/**/*.{test,spec}.{js,jsx}"],
    },
    server: {
      // All API calls are relative (/api/...) — the dev server proxies
      // them to the backend so the SPA and API share an origin and no
      // CORS setup is needed in development.
      proxy: {
        "/api": {
          target: apiProxyTarget,
        },
      },
    },
  };
});
