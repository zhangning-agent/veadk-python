import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/managed-agents/eventReducer.ts", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const moduleUrl = "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
const { emptyManagedEventState, managedConversationEntries, mergeManagedEvents } =
  await import(moduleUrl);

test("sorts and de-duplicates replayed events by sequence", () => {
  const first = mergeManagedEvents(emptyManagedEventState(), [
    { type: "session.status_idle", seq: 3 },
    { type: "user.message", id: "user-1", seq: 1, content: [{ type: "text", text: "hi" }] },
  ]);
  const replayed = mergeManagedEvents(first, [
    { type: "user.message", id: "user-1", seq: 1, content: [{ type: "text", text: "hi" }] },
    { type: "agent.message", id: "agent-1", seq: 2, content: [{ type: "text", text: "hello" }] },
  ]);

  assert.deepEqual(replayed.events.map((event) => event.seq), [1, 2, 3]);
  assert.equal(replayed.status, "idle");
  assert.equal(replayed.lastSequence, 3);
  assert.equal(replayed.lastEventId, "3");
});

test("marks a user message as running until idle or error", () => {
  const running = mergeManagedEvents(emptyManagedEventState("idle"), [
    { type: "user.message", seq: 1, content: [{ type: "text", text: "run" }] },
  ]);
  assert.equal(running.status, "running");
  assert.equal(
    mergeManagedEvents(running, [{ type: "session.status_idle", seq: 2 }]).status,
    "idle",
  );
  assert.equal(
    mergeManagedEvents(running, [{ type: "session.error", seq: 2, error: "failed" }]).status,
    "failed",
  );
});

test("replaces streamed assistant text with the canonical message", () => {
  const entries = managedConversationEntries([
    { type: "agent.message_stream_start", seq: 1, message_id: "message-1" },
    { type: "agent.message_chunk", seq: 2, message_id: "message-1", delta: "hel" },
    { type: "agent.message_chunk", seq: 3, message_id: "message-1", delta: "lo" },
    {
      type: "agent.message",
      id: "event-final",
      seq: 4,
      message_id: "message-1",
      content: [{ type: "text", text: "hello!" }],
    },
  ]);

  assert.deepEqual(entries, [
    { key: "seq:4", kind: "message", role: "assistant", text: "hello!" },
  ]);
});

test("pairs tool events and redacts event errors", () => {
  const entries = managedConversationEntries([
    { type: "agent.tool_use", id: "tool-1", seq: 1, name: "bash" },
    { type: "agent.tool_result", id: "result-1", seq: 2, tool_use_id: "tool-1" },
    { type: "session.error", seq: 3, error: "token=secret-value failed" },
  ]);

  assert.deepEqual(entries[0], {
    key: "seq:1",
    kind: "tool",
    name: "bash",
    state: "completed",
  });
  assert.match(entries[1].text, /<redacted>/);
  assert.doesNotMatch(entries[1].text, /secret-value/);
});
