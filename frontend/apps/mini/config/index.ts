import * as path from "node:path";
import { defineConfig, type UserConfigExport } from "@tarojs/cli";

const sharedPackagesRoot = path.resolve(__dirname, "../../../packages");
const apiPort = process.env.FAMILY_SPENDING_API_PORT ?? "18765";
const miniH5Port = Number(process.env.FAMILY_SPENDING_MINI_H5_PORT ?? "11087");
const apiTarget = `http://127.0.0.1:${apiPort}`;
const miniProgramApiBaseUrl = process.env.TARO_APP_API_BASE_URL?.trim() ?? "";

const config: UserConfigExport = {
  projectName: "family-spending-insights-mini",
  date: "2026-08-12",
  designWidth: 375,
  deviceRatio: {
    375: 2,
    640: 1.17,
    750: 1,
    828: 0.905,
  },
  sourceRoot: "src",
  outputRoot: "dist",
  framework: "react",
  compiler: {
    type: "webpack5",
    prebundle: {
      enable: false,
    },
  },
  cache: {
    enable: false,
  },
  // Compile the Mini Program API origin into client code without exposing a Node `process` global at runtime.
  defineConstants: {
    FAMILY_SPENDING_API_BASE_URL: JSON.stringify(miniProgramApiBaseUrl),
  },
  mini: {
    // Workspace packages export TypeScript source, so Taro must compile them as part of the app.
    compile: {
      include: [sharedPackagesRoot],
    },
    postcss: {
      pxtransform: {
        enable: true,
        config: {},
      },
      cssModules: {
        enable: false,
        config: {
          namingPattern: "module",
          generateScopedName: "[name]__[local]___[hash:base64:5]",
        },
      },
    },
  },
  h5: {
    publicPath: "/",
    staticDirectory: "static",
    router: {
      mode: "hash",
    },
    // H5 is a desktop development preview, not the Mini Program layout engine itself.
    postcss: {
      pxtransform: {
        enable: false,
        config: {},
      },
    },
    // H5 must compile the same shared workspace source as the Mini Program target.
    compile: {
      include: [sharedPackagesRoot],
    },
    devServer: {
      host: "127.0.0.1",
      port: miniH5Port,
      open: false,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  },
};

export default defineConfig(config);
