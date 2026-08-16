export type MiniEnvironmentVersion = "develop" | "trial" | "release";

const DEVELOPMENT_API_BASE_URL = "http://127.0.0.1:8765";

// Configure the deployed HTTPS API origin here once the domain and server are ready.
// Trial and release deliberately fail closed while this is empty.
const REMOTE_API_BASE_URL = "";

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

export function resolveApiBaseUrl(envVersion: MiniEnvironmentVersion): string {
  if (envVersion === "develop") {
    return DEVELOPMENT_API_BASE_URL;
  }

  const remote = normalizeBaseUrl(REMOTE_API_BASE_URL);
  if (!/^https:\/\//.test(remote)) {
    throw new Error("小程序试用版/正式版尚未配置 HTTPS API 地址");
  }
  return remote;
}

export function currentEnvironmentVersion(): MiniEnvironmentVersion {
  const value = wx.getAccountInfoSync().miniProgram.envVersion;
  if (value === "trial" || value === "release") {
    return value;
  }
  return "develop";
}
