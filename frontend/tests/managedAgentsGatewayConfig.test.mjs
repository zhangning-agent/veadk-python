import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const config = readFileSync(
  new URL(
    "../../examples/16_self_host_sandbox/nginx.managed-agents.conf.template",
    import.meta.url,
  ),
  "utf8",
);

test("official SDK session creation routes through ma-server", () => {
  const block = config.split("location = /v1/sessions {", 2)[1]?.split("}", 1)[0];
  assert.ok(block);
  assert.ok(block.includes("proxy_pass http://ma-server:8000"));
  assert.match(block, /MA_SERVER_API_TOKEN/);
});
