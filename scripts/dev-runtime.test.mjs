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

test("runtimeUrls keeps Desktop and API local surfaces explicit", () => {
  assert.deepEqual(runtimeUrls({ api: 18765, web: 15173 }), {
    api: "http://127.0.0.1:18765",
    web: "http://127.0.0.1:15173/overview",
  });
});

test("workerSignature ties ownership to role and runtime id", () => {
  assert.deepEqual(workerSignature("web", "runtime-123"), [
    "dev-runtime.mjs",
    "child",
    "web",
    "runtime-123",
  ]);
  assert.throws(() => workerSignature("mini", "runtime-123"), /Unknown runtime role/);
});

test("runtime state validation rejects stale Mini H5 state", () => {
  const valid = {
    schema_version: 2,
    runtime_id: "runtime-123",
    ports: { api: 18765, web: 15173 },
    urls: runtimeUrls({ api: 18765, web: 15173 }),
    processes: {
      api: { role: "api", pid: 101 },
      web: { role: "web", pid: 102 },
    },
  };
  assert.equal(isRuntimeState(valid), true);
  assert.equal(
    isRuntimeState({
      ...valid,
      processes: { ...valid.processes, mini: { role: "mini", pid: 103 } },
    }),
    false,
  );
});

test("workerCommand launches the API through the unified backend CLI", () => {
  const env = {
    FAMILY_SPENDING_API_PORT: "18765",
    PYTHONPATH: "existing-pythonpath",
  };

  const spec = workerCommand("api", env, "win32");
  assert.equal(spec.command, "uv");
  assert.deepEqual(spec.args, [
    "run",
    "--frozen",
    "python",
    "-m",
    "family_spending",
    "serve",
    "--port",
    "18765",
  ]);
  assert.match(spec.env.PYTHONPATH, /src/);
  assert.match(spec.env.PYTHONPATH, /existing-pythonpath/);
});

test("workerCommand launches Desktop through cmd.exe on Windows", () => {
  const env = {
    ComSpec: "C:\\Windows\\System32\\cmd.exe",
    FAMILY_SPENDING_API_PORT: "18765",
  };

  assert.deepEqual(workerCommand("web", env, "win32"), {
    command: env.ComSpec,
    args: ["/d", "/s", "/c", "npm run dev:web"],
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
