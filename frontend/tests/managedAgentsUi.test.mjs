import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(
  new URL("../src/managed-agents/ManagedAgentsApp.tsx", import.meta.url),
  "utf8",
);
const configSource = readFileSync(
  new URL("../src/managed-agents/config.ts", import.meta.url),
  "utf8",
);
const viteSource = readFileSync(
  new URL("../vite.managed-agents.config.ts", import.meta.url),
  "utf8",
);

test("uses the compact IME-aware composer and safe Markdown", () => {
  assert.match(appSource, /<CompactComposer/);
  assert.match(appSource, /<Markdown[\s\S]*?allowRawHtml=\{false\}/);
  assert.match(appSource, /busy=\{turnBusy\}/);
  assert.match(appSource, /disabled=\{composerDisabled \|\| interrupting\}/);
  assert.match(appSource, /onStop=\{interruptTurn\}/);
  assert.match(appSource, /client\.interruptSession/);
});

test("cancels stale transports and falls back to polling", () => {
  assert.match(appSource, /new AbortController\(\)/);
  assert.match(appSource, /controller\.abort\(new DOMException\("Session changed"/);
  assert.match(appSource, /SSE_RETRY_DELAYS_MS/);
  assert.match(appSource, /setTransport\("polling"\)/);
  assert.match(appSource, /pendingAfterSequenceRef/);
  assert.match(appSource, /sendAbortRef\.current\?\.abort/);
  assert.match(appSource, /不要直接重复发送/);
});

test("keeps errors accessible and prevents duplicate sends", () => {
  assert.match(appSource, /role="alert"/);
  assert.match(appSource, /aria-live="polite"/);
  assert.match(
    appSource,
    /if \(!message \|\| !selectedSessionId \|\| turnBusy \|\| composerDisabled\) return/,
  );
});

test("runtime config only accepts same-origin control and task API paths", () => {
  assert.match(configSource, /controlBasePath/);
  assert.match(configSource, /taskBasePath/);
  assert.match(configSource, /candidate\.startsWith\("\/"\)/);
  assert.match(configSource, /candidate\.includes\(":\/\/"\)/);
  assert.doesNotMatch(configSource, /apiKey|token|secret/i);
});

test("the independent Vite entry proxies control and task prefixes", () => {
  assert.match(viteSource, /managed-agents\/index\.html/);
  assert.match(viteSource, /"\/v1": apiProxy\(\)/);
  assert.match(viteSource, /"\/api": apiProxy\(\)/);
  assert.match(viteSource, /removeHeader\("authorization"\)/);
  assert.match(viteSource, /removeHeader\("x-top-account-id"\)/);
});

test("the workbench exposes Agent Environment and Session resources", () => {
  assert.match(appSource, /"agents", "environments", "sessions"/);
  assert.match(appSource, /client\.createEnvironment/);
  assert.match(appSource, /environment_id: sessionEnvironmentId/);
  assert.match(appSource, /ENVIRONMENT_ID_OVERRIDE=/);
  assert.match(appSource, /SANDBOX_PROVIDER=docker/);
});

const layoutSource = readFileSync(
  new URL("../src/managed-agents/managed-agents.css", import.meta.url), "utf8",
);

test("optional transport errors cannot move the transcript into the composer row", () => {
  assert.match(layoutSource, /\.managed-transcript\s*\{[^}]*grid-row:\s*3/);
  assert.match(layoutSource, /\.managed-composer\s*\{[^}]*grid-row:\s*4/);
});
