import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { build } from "esbuild";

const result = await build({
  entryPoints: [
    fileURLToPath(new URL("../src/adk/managedAgents.ts", import.meta.url)),
  ],
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node20",
  write: false,
});
const moduleUrl = "data:text/javascript;base64," + Buffer.from(
  result.outputFiles[0].contents,
).toString("base64");
const { ManagedAgentsClient } = await import(moduleUrl);

test("creates an Agent with the minimal tool-free contract", async () => {
  let request;
  const client = new ManagedAgentsClient({
    fetch: async (url, init) => {
      request = { url, init };
      return Response.json({
        id: "agent_1",
        type: "agent",
        name: "demo",
        model: { id: "model-1" },
        version: 1,
      }, { status: 201 });
    },
  });

  const agent = await client.createAgent({
    name: "demo",
    model: { id: "model-1" },
    system: "Be concise",
    tools: [],
  });

  assert.equal(agent.id, "agent_1");
  assert.equal(request.url, "/api/agents");
  assert.equal(request.init.method, "POST");
  assert.deepEqual(JSON.parse(request.init.body), {
    name: "demo",
    model: { id: "model-1" },
    system: "Be concise",
    tools: [],
  });
});

test("creates a cloud Session with the selected local environment id", async () => {
  let body;
  const client = new ManagedAgentsClient({
    fetch: async (_url, init) => {
      body = JSON.parse(init.body);
      return Response.json({ id: "session_1", type: "session", status: "pending" }, {
        status: 201,
      });
    },
  });

  await client.createSession({
    agent: "agent_1",
    environment_id: "env_local",
    title: "Hello",
  });
  assert.deepEqual(body, {
    agent: "agent_1",
    environment_id: "env_local",
    title: "Hello",
  });
});

test("creates and lists local Environments through the control prefix", async () => {
  const requests = [];
  const client = new ManagedAgentsClient({
    fetch: async (url, init = {}) => {
      requests.push({ url, init });
      if (init.method === "POST") return Response.json({
        id: "env_local",
        type: "environment",
        name: "Docker",
        status: "ready",
        config: { type: "selfhostsandbox", provider: "docker" },
      }, { status: 201 });
      return Response.json({ data: [] });
    },
  });

  await client.listEnvironments();
  await client.createEnvironment({ name: "Docker", provider: "docker" });
  assert.equal(requests[0].url, "/api/environments?limit=100");
  assert.equal(requests[1].url, "/api/environments");
});

test("sends user.message to an encoded Session path and accepts an empty 202", async () => {
  let request;
  const client = new ManagedAgentsClient({
    fetch: async (url, init) => {
      request = { url, init };
      return new Response(null, { status: 202 });
    },
  });

  await client.sendUserMessage("session/unsafe", "hello");
  assert.equal(request.url, "/api/sessions/session%2Funsafe/events");
  assert.deepEqual(JSON.parse(request.init.body), {
    events: [{ type: "user.message", content: [{ type: "text", text: "hello" }] }],
  });
});

test("streams replay with Last-Event-ID and forwards cancellation", async () => {
  let request;
  const client = new ManagedAgentsClient({
    fetch: async (url, init) => {
      request = { url, init };
      return new Response('data: {"type":"session.status_idle","seq":7}\n\n', {
        headers: { "content-type": "text/event-stream" },
      });
    },
  });
  const controller = new AbortController();
  let opened = false;
  const events = [];
  for await (const event of client.streamEvents("session_1", {
    signal: controller.signal,
    lastEventId: "6",
    onOpen: () => {
      opened = true;
    },
  })) {
    events.push(event);
  }

  assert.equal(request.url, "/v1/sessions/session_1/events/stream?replay=1");
  assert.equal(request.init.headers["Last-Event-ID"], "6");
  assert.equal(request.init.signal, controller.signal);
  assert.equal(opened, true);
  assert.deepEqual(events, [{ type: "session.status_idle", seq: 7 }]);
});

test("reports non-JSON responses without exposing credential values", async () => {
  const client = new ManagedAgentsClient({
    fetch: async () => new Response("gateway token=super-secret unavailable", {
      status: 502,
      headers: { "content-type": "text/plain" },
    }),
  });

  await assert.rejects(client.listAgents(), (error) => {
    assert.match(error.message, /HTTP 502/);
    assert.match(error.message, /非 JSON/);
    assert.doesNotMatch(error.message, /super-secret/);
    return true;
  });
});

test("redacts credentials from structured API errors", async () => {
  const client = new ManagedAgentsClient({
    fetch: async () => Response.json(
      { detail: "authorization=super-secret rejected" },
      { status: 401 },
    ),
  });

  await assert.rejects(client.listAgents(), (error) => {
    assert.match(error.message, /authorization=<redacted>/i);
    assert.doesNotMatch(error.message, /super-secret/);
    return true;
  });
});

test("rejects a cross-origin API base path", () => {
  assert.throws(
    () => new ManagedAgentsClient({ taskBasePath: "https://example.com/v1" }),
    /同源绝对路径/,
  );
});
