import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { build } from "esbuild";

const result = await build({
  entryPoints: [
    fileURLToPath(new URL("../src/managed-agents/eventReducer.ts", import.meta.url)),
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
const { emptyManagedEventState, managedConversationEntries, mergeManagedEvents } =
  await import(moduleUrl);

test("folds official Anthropic preview events into one streaming message", () => {
  const state = mergeManagedEvents(emptyManagedEventState(), [
    { type: "event_start", event: { type: "agent.message", id: "sevt-1" } },
    {
      type: "event_delta",
      event_id: "sevt-1",
      delta: { type: "content_delta", content: { type: "text", text: "hel" } },
    },
    {
      type: "event_delta",
      event_id: "sevt-1",
      delta: { type: "content_delta", content: { type: "text", text: "lo" } },
    },
  ]);

  assert.deepEqual(managedConversationEntries(state.events), [
    {
      key: "agent.message_stream_start::sevt-1:::",
      kind: "message",
      role: "assistant",
      text: "hello",
      streaming: true,
    },
  ]);
});

test("folds thinking fragments into one block for each user turn", () => {
  const entries = managedConversationEntries([
    {
      type: "user.message",
      id: "user-1",
      content: [{ type: "text", text: "first" }],
    },
    { type: "agent.thinking", id: "thinking-1", thinking_id: "thinking-1", text: "分析" },
    { type: "agent.thinking", id: "thinking-2", thinking_id: "thinking-2", text: "需求" },
    { type: "agent.tool_use", id: "tool-1", name: "bash" },
    { type: "agent.thinking", id: "thinking-3", thinking_id: "thinking-3", text: "，继续" },
    { type: "agent.tool_result", id: "result-1", tool_use_id: "tool-1" },
    {
      type: "agent.message",
      id: "answer-1",
      content: [{ type: "text", text: "done" }],
    },
    {
      type: "user.message",
      id: "user-2",
      content: [{ type: "text", text: "second" }],
    },
    { type: "agent.thinking", id: "thinking-4", thinking_id: "thinking-4", text: "新一轮" },
  ]);

  assert.deepEqual(entries.map((entry) => entry.kind), [
    "message",
    "thinking",
    "tool",
    "message",
    "message",
    "thinking",
  ]);
  assert.deepEqual(
    entries.filter((entry) => entry.kind === "thinking").map((entry) => entry.text),
    ["分析需求，继续", "新一轮"],
  );
});

test("reconciles streamed and canonical thinking without duplicate blocks or text", () => {
  const entries = managedConversationEntries([
    { type: "user.message", id: "user-1", content: "go" },
    { type: "agent.thinking_stream_start", thinking_id: "thinking-stream" },
    { type: "agent.thinking_chunk", thinking_id: "thinking-stream", delta: "先分析" },
    { type: "agent.thinking_chunk", thinking_id: "thinking-stream", delta: "问题" },
    { type: "agent.thinking_stream_end", thinking_id: "thinking-stream" },
    { type: "agent.thinking", id: "thinking-1", thinking_id: "thinking-stream", text: "先分析" },
    { type: "agent.thinking", id: "thinking-2", thinking_id: "thinking-2", text: "问题" },
  ]);

  assert.deepEqual(entries, [
    { key: "id:user-1", kind: "message", role: "user", text: "go" },
    {
      key: "agent.thinking_stream_start:::thinking-stream::",
      kind: "thinking",
      text: "先分析问题",
      streaming: false,
    },
  ]);
});
