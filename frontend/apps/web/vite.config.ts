import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "FAMILY_SPENDING_");
  const apiPort = env.FAMILY_SPENDING_API_PORT || "18765";
  const webPort = Number(env.FAMILY_SPENDING_WEB_PORT || "15173");
  const apiTarget = `http://127.0.0.1:${apiPort}`;

  return {
    plugins: [tailwindcss(), react()],
    server: {
      port: webPort,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: false,
        },
      },
    },
  };
});
