import assert from "node:assert/strict";
import test from "node:test";

import {
  isRuntimeState,
  parsePreferredPort,
  runtimeUrls,
  workerCommand,
  workerSignature,
} from "./dev-runtime.mjs";

test("parsePreferredPort uses defaults and validates explicit values", () => {
  assert.equal(parsePreferredPort(undefined, 18765), 18765);
  assert.equal(parsePreferredPort("15190", 15173), 15190);
  assert.throws(() => parsePreferredPort("0", 15173), /Invalid local development port/);
  assert.throws(() => parsePreferredPort("abc", 15173), /Invalid local development port/);
});

test("runtimeUrls keeps the three local surfaces explicit", () => {
  assert.deepEqual(runtimeUrls({ api: 18765, web: 15173, mini: 11087 }), {
    api: "http://127.0.0.1:18765",
    web: "http://127.0.0.1:15173/overview",
    mini: "http://127.0.0.1:11087/",
  });
});

test("workerSignature ties ownership to role and runtime id", () => {
  assert.deepEqual(workerSignature("web", "runtime-123"), [
    "dev-runtime.mjs",
    "child",
    "web",
    "runtime-123",
  ]);
  assert.throws(() => workerSignature("other", "runtime-123"), /Unknown runtime role/);
});

test("runtime state validation rejects malformed process ownership data", () => {
  const valid = {
    schema_version: 1,
    runtime_id: "runtime-123",
    ports: { api: 18765, web: 15173, mini: 11087 },
    urls: runtimeUrls({ api: 18765, web: 15173, mini: 11087 }),
    processes: {
      api: { role: "api", pid: 101 },
      web: { role: "web", pid: 102 },
      mini: { role: "mini", pid: 103 },
    },
  };
  assert.equal(isRuntimeState(valid), true);
  assert.equal(
    isRuntimeState({
      ...valid,
      processes: { ...valid.processes, web: { role: "mini", pid: 102 } },
    }),
    false,
  );
});


test("workerCommand launches npm scripts through cmd.exe on Windows", () => {
  const env = {
    ComSpec: "C:\\Windows\\System32\\cmd.exe",
    FAMILY_SPENDING_API_PORT: "18765",
  };

  assert.deepEqual(workerCommand("web", env, "win32"), {
    command: env.ComSpec,
    args: ["/d", "/s", "/c", "npm run dev:web"],
    env,
  });
  assert.deepEqual(workerCommand("mini", env, "win32"), {
    command: env.ComSpec,
    args: ["/d", "/s", "/c", "npm run dev:mini:h5"],
    env,
  });
});

test("workerCommand keeps direct npm execution on non-Windows platforms", () => {
  const env = { FAMILY_SPENDING_API_PORT: "18765" };
  assert.deepEqual(workerCommand("web", env, "linux"), {
    command: "npm",
    args: ["run", "dev:web"],
    env,
  });
});
