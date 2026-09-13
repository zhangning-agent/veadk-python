# AgentKit Streaming and Lifecycle E2E Log

This file is the append-only implementation and verification log for the
Managed Agents streaming, interruption, sandbox lifecycle, and concurrency
work. Secrets and complete authorization-bearing endpoints must never be
recorded here.

## Acceptance checklist

- [x] The public gateway streams conversation events before the final message.
- [x] Conversation state is visible and an executing turn can be interrupted
  server-side.
- [x] An idle AgentKit sandbox is reclaimed, and the same Managed Agents
  Session can start a new sandbox after at least five minutes.
- [x] A user message that needs no tool does not start an AgentKit sandbox; the
  first tool call starts it.
- [x] All implementation, deployment, and verification changes are recorded
  in this file as they happen.
- [x] The official Anthropic Python SDK completes a streamed conversation and
  a real tool call through the public gateway.
- [x] Forty concurrent official-SDK conversations complete without Session,
  event, tool-result, or marker cross-talk.

## 2026-09-12 - Baseline audit

### Scope

- VeADK example: `examples/16_self_host_sandbox`
- ma-server: `/home/mofanke/github/agent-ma/ma-server`
- Dispatcher: `/home/mofanke/gitcode/actb-mono/sandboxes/self-host-sandbox/src/dispatcher`
- AgentKit Runtime: `r-yetovao0sgqgapsacusi`
- AgentKit Tool: `t-yetov9c9hce5g253o7oj`
- Public gateway: `http://skv8hsls9otpqehqgo61o.apigateway-cn-beijing.volceapi.com/`

### Current-state evidence

- Gateway `/`, `/healthz`, and `/managed-agents-config.json` returned HTTP 200.
- K8s Gateway, ma-server, Task Server, and Agent Loop Deployments were Ready.
- AgentKit Runtime and Tool reported `Ready`.
- Runtime image: `agentkit-selfhostsandbox:runtime-0.0.16`.
- Tool image: `agentkit-selfhostsandbox:tool-skills-0.0.16-oma-session-01`.
- Runtime capacity was `MaxConcurrency=10`, `MinInstance=1`, `MaxInstance=1`;
  this cannot by itself prove forty simultaneously executing conversations.
- Two Ready Tool Sessions existed during the audit. Identifiers and signed
  endpoints are intentionally omitted from this log.
- The remote Agent Loop deployment used `MANAGED_AGENT_TOOL_EXECUTION=remote`.
- Runtime logs showed real bash tool execution and a matching final response.

### Baseline tests

```text
cd /home/mofanke/github/veadk-python-zhangning-agentloop
.venv/bin/pytest -q tests/runtime/test_managed_agent_loop.py \
  tests/runtime/test_self_host_sandbox_agent.py
Result: 31 passed, 4 warnings
```

ma-server test collection failed before executing tests because its existing
local `.venv` did not contain SQLAlchemy. This is an environment dependency
gap, not a product assertion failure. It must be repaired before the final
ma-server regression run.

### Existing uncommitted work preserved

The VeADK and `actb-mono` worktrees already contained requirement-related
changes before this implementation pass. They are being preserved and reviewed
in place. In particular, the current changes include remote tool-result waiting,
AgentKit idle-session reclamation, work-lease extension before provisioning,
and OMA `updated_at=null` compatibility.

### Security note

The AgentKit Tool query helper currently prints authorization-bearing values
and signed Session endpoints in raw output. None are copied into this file. A
future helper hardening or credential rotation should be considered separately;
it is not required to establish the conversation behavior in this checklist.

## 2026-09-12 - Implementation pass 1

### Changes

- Task Server sandbox work is now enqueued by `agent.tool_use`, not by
  `user.message`. Pure model turns no longer request an AgentKit sandbox.
- Active work is deduplicated by `environment_id + session_id`; a stopped work
  item no longer prevents a later tool call from creating replacement work.
- `user.interrupt` is persisted and broadcast, and both the ma-server Agent
  Loop lease and the Task Server sandbox work are moved to a stopping state.
- A self-hosted-worker-only transient event endpoint broadcasts model chunks
  without adding them to canonical Session history.
- VeADK runs ADK with `StreamingMode.SSE`, publishes transient message and
  thinking chunks, and reuses the preview ID for the final canonical event.
- The Task Server maps transient OMA chunks to official Anthropic
  `event_start` / `event_delta` frames when `event_deltas` is requested.
- The Web UI subscribes to transient chunks and exposes a real interrupt
  action while the turn is running.
- AgentKit Dispatcher treats `user.interrupt` followed by idle as terminal for
  the physical sandbox and deletes that sandbox instead of waiting out the
  idle grace period.
- Agent Loop work heartbeat interval defaults to two seconds so an explicit
  interrupt reaches the running model/tool path promptly.
- The public gateway now proxies `POST /v1/sessions` through ma-server, which
  lets an official Anthropic SDK client create, stream, and continue Sessions
  without using the browser-only `/api` client.

### Focused verification

```text
VeADK managed loop and sandbox tests: 33 passed
ma-server work/API/contract tests: 25 passed, 1 skipped
oma-test self-host streaming tests: 4 passed
oma-test http-routes TypeScript check: passed
oma-test main-node TypeScript check: passed
actb-mono common self-host worker Go tests: passed
actb-mono dispatcher Go tests: passed
```

The frontend client/UI test pass contained one assertion typo in a synthetic
event key; product behavior was correct. The expected key was corrected before
the next full frontend run. The initial frontend build command was run from the
repository root, which has no `package.json`; subsequent frontend commands run
from `frontend/`.

### Build-context safety pitfall

The deployed Task Server source checkout and ma-server checkout did not have a
root `.dockerignore`. Before building, both contexts received explicit ignores
for real environment files, `.dev.vars`, keys/certificates, cloud credential
directories, Git metadata, virtual environments, dependency caches, and test
artifacts. This prevents local credentials and large caches from being sent to
the Docker daemon.

### ma-server dependency-layer pitfall

The first `20260912-stream-lifecycle-01` ma-server image used
`Dockerfile.deploy`, which overlays source on a fixed older digest. That base
did not contain the newly declared SQLAlchemy dependency, so the new Pod failed
at import with `ModuleNotFoundError: sqlalchemy`. The Deployment was immediately
restored to `20260911-pg-02` and became Ready. The corrected image is rebuilt
from the full `Dockerfile`, which performs `uv sync --frozen --extra agentkit`;
the overlay image is not used for this dependency-changing release.

## 2026-09-12 - Public gateway verification pass 1

Before the first official-SDK smoke request, the AgentKit Tool control plane
reported zero physical Sessions. The E2E runner then failed during local helper
initialization, before sending any gateway request: loading
`agentkit_tool_deploy.py` by file path did not make its sibling
`requests_volcauth` module importable. The runner now adds the helper's own
directory to `sys.path` before executing that module. This is a test-harness
fix only; it does not change gateway, Session, or sandbox behavior.
Python 3.12 additionally requires a dynamically loaded dataclass module to be
registered in `sys.modules` before execution; the runner now follows that
standard import sequence as well. Both initialization failures happened before
any SDK Session or event request was emitted.

The first request that reached the public gateway failed with HTTP 500.
ma-server logs showed that its local Agent lookup and snapshot pinning passed,
but Task Server returned `404 Agent not found`: the split control plane and
Task Server intentionally have separate Agent registries. Task Server Session
creation now has an explicit deployment opt-in for complete
`agent_with_overrides` snapshots. The opt-in only accepts an object with an ID,
model, and tools; ordinary string references and incomplete objects still use
the Task Server registry and retain the existing 404 behavior. The K8s Task
Server enables this trust boundary for the ma-server-backed topology.
The focused Task Server suite passed 7 tests and the main-node TypeScript
check passed. Kustomize also passed server-side dry-run. This checkout does
not install a `prettier` executable, so a requested Prettier check could not
run; `git diff --check` passed instead.
After deploying the first Agent-only compatibility pass, Session creation
advanced past Agent validation and Task Server returned `Environment not
found`. The same split-registry rule applies to ma-server's local Environment.
ma-server now forwards its already-validated Environment as an SDK
`extra_body` snapshot. Task Server's explicit trust opt-in accepts it only when
the snapshot ID equals `environment_id` and its execution type is
`selfhostsandbox`; mismatched, cloud, or incomplete snapshots remain invalid.
The follow-up verification passed: ma-server API tests 13/13, Task Server
focused tests 8/8, main-node TypeScript check, both repositories' diff checks,
and K8s server-side dry-run.

Session creation then succeeded and the first pure-text turn completed
canonically, but every transient publish logged HTTP 500 even though Task
Server had already broadcast the preview. Both Worker-to-ma-server and
ma-server-to-Task-Server low-level SDK calls used unparameterized
`cast_to=dict`; Anthropic SDK 1.3.0 cannot construct that bare generic and
raised `ValueError: not enough values to unpack`. Both proxy layers now use
`dict[str, Any]`, which preserves the accepted 202 response without a false
500. The ma-server rollout also temporarily severed the long-poll connection
held by the existing Agent Loop Pod; restarting that Deployment recovered and
claimed the queued work. Future paired rollouts must restart the Agent Loop
after ma-server is Ready.
The interrupted pre-fix smoke had already completed its pure-text canonical
turn with the exact marker, running and idle states, while the physical Tool
Session count remained zero. Its preview could not be accepted as streaming
evidence because every transient request hit the `cast_to` parsing error.

### Official SDK smoke: passed

With ma-server `20260912-stream-lifecycle-04`, Task Server
`20260912-stream-lifecycle-04`, and Agent Loop Worker
`20260912-stream-lifecycle-03`, the official Python SDK smoke completed both
turns successfully:

- pure-text turn: running and idle states observed; first `event_delta` at
  approximately 5.01 seconds and final `agent.message` at 5.40 seconds; no
  physical Tool Session existed for that logical Session after the turn;
- bash-tool turn: `agent.tool_use` at approximately 4.04 seconds, matching
  `agent.tool_result` at 11.23 seconds, first final-message delta at 15.31
  seconds, and final idle at 15.67 seconds;
- the tool-use/result IDs matched, both unique markers appeared in the final
  response, and the physical AgentKit Session had the same logical
  `UserSessionId`.

Evidence JSON is stored outside the repository at
`/tmp/managed-agents-goal-20260912/anthropic-smoke-before-agentkit-update.json`.
The SDK/httpcore stack emitted a non-fatal asynchronous-generator-close
warning after the successful assertions; final regression will verify whether
explicit stream closure removes that cleanup warning.

### First live interrupt attempt: partial failure and diagnosis

The `sleep 120` bash command was genuinely canceled and returned `context
canceled` without the marker. The logical Session returned to idle, but the
physical AgentKit Session did not disappear within the 60-second assertion, so
the interrupt mode correctly failed. The real event sequence appended tool
result, explanation, span end, and a second idle after the immediate
interrupt-idle pair. Runtime `0.0.17` only recognized an interrupt when those
were the newest two events. The state scanner now searches backward within the
current user turn: any interrupt followed by idle is terminal, while a newer
`user.message` fences off interrupts from older turns. Regression tests cover
both the real post-cancellation sequence and a resumed Session.

### Runtime interrupt fix and retest: passed

Runtime and Tool were advanced together to `0.0.18`; the Tool image is an
identical retag of the already verified `0.0.17` digest, while the Runtime
contains the event-window scanner fix. Runtime remained `MaxConcurrency=40`.
The official SDK interrupt mode then passed: it observed a real bash
`agent.tool_use`, waited for the bound physical AgentKit Session, sent
`user.interrupt`, and observed idle without ever receiving the command's
post-sleep marker. The Worker log recorded the shell result as `context
canceled`, and the physical AgentKit Session disappeared within the test's
60-second cleanup bound. Total observation time was approximately 49.1 seconds;
the reported 32.4-second interrupt-to-cleanup interval includes waiting for
physical Session absence after the immediate logical idle event.

Evidence JSON: `/tmp/managed-agents-goal-20260912/anthropic-interrupt-agentkit-0018.json`.

The 40-concurrency acceptance now also inspects the AgentKit control plane
after all gated turns complete. It requires exactly forty retained physical
Sessions whose `UserSessionId` values match the forty logical Sessions and
requires all forty physical IDs to be unique. This supplements the simultaneous
stream-send gate and cross-marker checks with direct one-sandbox-per-Session
evidence. Signed endpoints are excluded from the saved result.
Each concurrent bash call also records its own start/end epoch milliseconds
around a 30-second sleep. The test computes the maximum overlap of those forty
intervals and requires it to equal forty, distinguishing genuinely in-flight
tool execution from forty clients that were only queued.
An initial attempt concurrently created all forty Session resources, but the
public ingress only completed nineteen creations before the remainder stalled
at the HTTP connection layer. No turns or Tool Sessions had started, so this
did not exercise execution capacity. The final test creates the forty logical
Sessions sequentially, then simultaneously opens their official SDK streams
and releases all forty sends through one gate; execution concurrency and
isolation assertions are unchanged.
The first execution attempt then proved all forty works were claimed and forty
physical Sessions were created, but the Runtime's unpaced two-second
`GetSession` loops exceeded AgentKit OpenAPI account flow limits. Repeated
`AccountFlowLimitExceeded` 429s also caused a few early provisioning failures.
The shared AgentKit control client now serializes request starts at a default
250 ms interval and retries 429/flow-limit responses with bounded,
context-cancellable exponential backoff. This throttles control-plane polling
only; model calls and the 30-second bash payloads remain concurrent.
Runtime `0.0.20`, containing this mitigation, was built and pushed with digest
`sha256:c610d9827604a42961256fa5e8de2b79d4e9ea1d8bb75e5ee5e2151eb1d3e210`.
Before deploying it, the cleanup command initially stopped locally because
this host does not install `jq`; no DeleteSession request had been issued.
Cleanup therefore uses the existing signed AgentKit Python helper directly
and emits only Session IDs, states, and logical bindings. The final concurrency
payload now holds each bash execution for a configurable 120 seconds rather
than 30 seconds, leaving enough overlap margin while 40 control-plane Session
starts are deliberately paced.

### First live five-minute lifecycle attempt: failed and diagnosed

The first tool turn succeeded and created a bound physical sandbox, but the
sandbox was still present after the five-minute reclaim timeout. Direct API
comparison proved that Task Server returned events in ascending sequence for
both `order=asc` and `order=desc`. Runtime therefore treated the oldest
`user.message` as the latest state and never began its three-minute idle grace.
The Node Session router now honors descending order. Runtime is also hardened
to sort a response by its wire `seq` field (falling back to `processed_at`)
before deriving current-turn idle/interrupt state, so reclamation no longer
depends on a server preserving array direction.

### Five-minute lifecycle retest: passed

The corrected Runtime logged the idle grace start and the three-minute grace
expiration, after which the Tool control plane returned zero Sessions. The
test kept the same logical Session idle until the full five-minute boundary,
then executed a second bash turn successfully. The first and second physical
AgentKit Session IDs were different, proving reclaim followed by lazy recreate.
Both turns streamed deltas before their canonical final messages and had
matching tool-use/result IDs. Evidence JSON:
`/tmp/managed-agents-goal-20260912/anthropic-lifecycle-agentkit-0019-final2.json`.

## 2026-09-12 - AgentKit Runtime and Tool deployment

- Built and pushed matching `0.0.17` images after confirming the Dockerfile-
  specific ignore files exclude `.env`, keys, certificates, cloud credential
  directories, and Git metadata while retaining the Runtime source tree.
- Runtime image digest: `sha256:4e21be250c77e68758d0cad8c51a79d12689b3705a311d9e90b359201865c4a1`.
- Skills Tool image digest: `sha256:9ac4ac917a78b2844ad1fc8e566fa07e8b56996643d14a9b2b6d5706c74ef3a5`.
- Deleted the smoke-created physical Tool Sessions before rollout so the new
  image starts from an empty instance baseline.
- Updated Tool `t-yetov9c9hce5g253o7oj` to `tool-skills-0.0.17` and waited for
  `Ready`, without creating a replacement instance.
- Updated Runtime `r-yetovao0sgqgapsacusi` to `runtime-0.0.17` and changed
  `MaxConcurrency` from 10 to 40 in the same published version. Runtime
  returned `Ready` with both values verified. Existing Runtime env values were
  preserved, and their plaintext was never written to this log.

Pre-build Go checks passed for dispatcher and shared Worker packages. The
self-host-sandbox Python contract suite ran 32 tests: 31 passed, 1 skipped, and
1 failed in a pre-existing DeliveryAI-only assertion that expects the
supervisor command to start directly with `/usr/local/bin/ant`; the current
DeliveryAI config intentionally wraps it with `wait-git-credentials.sh`. This
does not cover either image deployed here and was not changed.

### AgentKit `0.0.20` paced-control-plane deployment

- The aborted load left one physical Tool Session. It was deleted before the
  rollout, and a fresh ListSessions call returned an empty baseline.
- The AgentKit-backed Agent Loop Deployment was scaled to zero during the image
  transition so no old poller could create a sandbox between cleanup and the
  paired rollout.
- Tool `t-yetov9c9hce5g253o7oj` advanced to `tool-skills-0.0.20`, returned
  `Ready`, and no convenience test instance was created. The image is the same
  verified Tool digest as `0.0.19`.
- Runtime `r-yetovao0sgqgapsacusi` advanced to `runtime-0.0.20`, returned
  `Ready`, and retained all 16 existing environment entries without exposing
  their values. A redacted readback verified `MaxConcurrency=40`,
  `MinInstance=1`, and `MaxInstance=1`.
- The AgentKit Agent Loop was scaled back to one and explicitly restarted after
  both dependencies were Ready. Fifteen seconds after rollout, the Worker was
  Ready and ListSessions still returned zero.

### First `0.0.20` 40-way run: failed the physical-retention assertion

All forty official SDK streams produced distinct bash tool calls within about
four seconds, and the Worker log contained no `AccountFlowLimitExceeded` or
HTTP 429 entry. The paced Runtime grew the control-plane view to 36 Ready
physical Sessions with 36 unique physical IDs and 36 unique logical bindings.
All forty turns then completed, but the sampler never observed the four
remaining logical Sessions at the same time as those 36, so the harness exited
non-zero at the exact 40-retained-sandbox assertion and deliberately did not
write a passed JSON result. The missing logical IDs are retained in the local
stderr artifact for diagnosis. The official SDK/httpcore stack also emitted
asynchronous-generator close errors while unwinding the failed 40-stream run;
these are a separate client cleanup issue and not accepted as harmless until
retested.
Runtime instance logs identified the exact failure: the four missing attempts
returned `408 RequestTimeout.CreatePrivateImageSession` after roughly 35 to 40
seconds. The Runtime's one recovery lookup found no Session and immediately
stopped those work leases, which propagated cancellation into the four bash
processes even though each lease had just been renewed. A diagnostic 37th
Session created successfully alongside the retained 36 and was immediately
deleted, ruling out a fixed 36-Session account cap. Runtime creation now treats
an absent Session after this ambiguous 408 as retryable: it renews the work
lease, uses bounded cancellable backoff, and retries up to three times while
still recovering a late-created Session by exact `UserSessionId`. The final
payload uses a 90-second hold, which remains longer than the observed sandbox
creation spread but stays below the remote tool's 120-second execution limit.
The retry regression and the full Go dispatcher package passed. Runtime
`0.0.21` was built and pushed as
`sha256:8b4b6f12b2a8d71a322e633b2bbb4df2830a2574cc4c0055895b9bb07d3b4773`;
the matching Tool tag `tool-skills-0.0.21` points to the unchanged verified
digest `sha256:9ac4ac917a78b2844ad1fc8e566fa07e8b56996643d14a9b2b6d5706c74ef3a5`.
Both resources returned `Ready`; Runtime readback again verified 40 concurrency,
one reserved instance, one maximum instance, and 16 preserved environment
entries. The Agent Loop was restarted only after both were Ready, and its
post-restart physical Session baseline remained zero.

The official SDK's asynchronous stream wrapper also now closes its internal
event iterator before closing the HTTP response. This makes early exit on the
canonical idle event deterministic under many simultaneous streams. The E2E
runner uses the public stream context manager directly. The focused SDK
streaming and environment-dispatcher suite passed 29 tests after clearing host
SOCKS proxy variables; the first run could not construct the test clients
because that test venv lacks the optional `socksio` dependency. A refreshed
SDK wheel was written to the managed-agent Docker staging directory.
The post-deploy official SDK smoke then passed both a pure-text turn and a real
bash turn. Both streamed a non-empty delta before the final message; the tool
use/result IDs matched and the physical Session's `UserSessionId` matched the
logical Session. The pure-text turn created no sandbox. The harness stderr was
empty, confirming that the SDK stream cleanup warning no longer reproduced.
Evidence: `/tmp/managed-agents-goal-20260912/anthropic-smoke-agentkit-0021.json`.
The single smoke sandbox was deleted and ListSessions returned zero before the
next concurrency run.

### Second 40-way run: one active sandbox terminated

Runtime `0.0.21` successfully created all forty physical Sessions over a
23.7-second span, with no CreateSession 408 and no sandbox-worker error in the
Runtime log. One physical Session then disappeared about 18 seconds after its
creation while the logical Session still had an active bash tool call. The
Runtime treated the physical terminal state as a normal end, stopped the fresh
work lease, and the remote tool returned `context canceled`. The other 39
physical Sessions remained Ready and uniquely bound, so the E2E harness again
failed the exact 40-retained assertion. Its stderr contained only that assertion
and no asynchronous-generator close warnings.

Runtime physical-session monitoring now distinguishes logical state from
physical state. A physical NotFound, Failed, Error, Terminating, Terminated, or
Deleted state while the logical turn is still active triggers bounded
reprovisioning under the same work lease and `UserSessionId`. Logical idle and
interrupt/terminated paths retain their existing cleanup semantics. A focused
regression simulates an active physical termination, verifies replacement
creation, then proves terminal logical state stops recovery; the dispatcher
package passes.
The physical Tool Worker also previously force-stopped its work from SDK
cleanup after the hosting platform canceled the process. That happened before
the outer Runtime observed physical termination, making its lease impossible to
recover. The OMA Worker API is now wrapped so StopWork is suppressed only when
the process root context is already canceled. Normal tool completion continues
to stop work, while user interruption is still stopped explicitly by Task
Server before process shutdown. A focused wrapper test covers both forwarding
and suppression branches.
Runtime and Tool `0.0.23` were then rebuilt from the updated sources and pushed.
Runtime digest: `sha256:a6ad321dc0718313f41818ca3d25f7e008aacac75c43460881f118cbc83cca2c`;
Tool digest: `sha256:a8de70960b5bbd3917c0f306692d5233b232153d0832d9db800d376d9601c2e4`.
Both returned Ready, Runtime retained its 40 concurrency and 16 environment
entries, and the restarted Agent Loop observed a zero-Session baseline.
The rebuilt `0.0.23` Tool then passed the official SDK smoke with one matching
tool use/result and no stderr output.

### Final 40-concurrent isolated Session acceptance: passed

The strengthened official Anthropic Python SDK run passed against Runtime and
Tool `0.0.23`:

- 40 distinct logical Sessions opened their event streams before a shared send
  gate released all turns;
- AgentKit control-plane sampling observed 40 simultaneous Ready physical
  Sessions, 40 unique physical IDs, and 40 unique logical `UserSessionId`
  bindings;
- every Session completed one real bash tool use and matching tool result;
- server-recorded bash intervals were 90.002 to 90.030 seconds long, with all
  forty overlapping for 68.874 seconds;
- all forty streams produced a non-empty delta before their canonical final
  message, and the cross-marker scan found no marker from another Session;
- the run completed in approximately 124.35 seconds with empty SDK stderr;
- Runtime logs contained no 408, 429, FlowLimit, or sandbox-worker error, and
  Tool logs contained no context cancellation or tool timeout.

Evidence JSON:
`/tmp/managed-agents-goal-20260912/anthropic-concurrency-40-agentkit-0023.json`.
All forty concurrency-test physical Sessions were deleted immediately after
the passed run; the Agent Loop was restarted and ListSessions returned zero.

### Final `0.0.23` lifecycle regressions: passed

- Interrupt: after observing a real bash tool use and its bound physical
  Session, the official SDK sent `user.interrupt`. The logical Session returned
  to idle in approximately 3.44 seconds, the post-sleep marker never appeared,
  and the physical Session was deleted inside the 60-second bound.
- Idle reclaim/recreate: the first physical Session disappeared after the
  configured three-minute idle grace. At the full five-minute boundary, the
  same logical Session completed a second real bash turn in a new physical
  Session with a different ID. Both turns had matching tool-use/result IDs and
  streamed deltas before final messages.
- Both final harnesses produced empty stderr. Evidence is stored at
  `/tmp/managed-agents-goal-20260912/anthropic-interrupt-agentkit-0023.json`
  and `/tmp/managed-agents-goal-20260912/anthropic-lifecycle-agentkit-0023.json`.
- The final lifecycle sandbox was deleted, leaving zero Tool Sessions.

## Final regression and deployment audit

- VeADK managed loop and sandbox tests: 33 passed.
- Managed Agents frontend focused tests: 19 passed; production TypeScript/Vite
  build passed. The full frontend suite passed all 991 tests after setting
  `CHOKIDAR_USEPOLLING=true`. Without polling, the host's 128 inotify-instance
  limit caused 10 Vite watcher `EMFILE` failures after 981 passes; rerunning the
  two affected files independently also passed 1 and 19 tests respectively.
- ma-server focused API/work-store tests: 22 passed, 1 skipped; Ruff passed.
- OMA Task Server focused streaming tests: 8 passed; node-session-router tests:
  3 passed; main-node TypeScript check passed. The available Node runtime is 22
  while the package requests 24, so pnpm emitted an engine warning only.
- Anthropic Python SDK streaming, event, and bounded-dispatch tests: 36 passed.
- ACTB Runtime dispatcher and shared Tool Worker Go packages: all tests passed.
- Managed Agents K8s manifests passed `kubectl apply --dry-run=server`.
- Source and evidence-file `git diff --check` passed. Generated minified Web UI
  output is excluded from this whitespace check because it contains upstream
  minifier whitespace. No commit or Merge Request was created.
- Secret-pattern scanning found no signed endpoint or cloud credential literal
  in this log or the E2E harness. Deployment snapshots containing environment
  values remain mode `0600` outside the repository.
- Final live state: public health and Managed Agents config endpoints return
  HTTP 200; Gateway 2/2, ma-server 1/1, Task Server 1/1, and Agent Loop 1/1 are
  Ready; AgentKit Runtime and Tool `0.0.23` are Ready; Runtime capacity is 40;
  and ListSessions returns zero after test cleanup.

## Prompt-to-artifact completion audit

| Requirement | Implementation artifact | Direct acceptance evidence | Result |
| --- | --- | --- | --- |
| 1. Public-gateway streamed conversation | `managed_agent_loop.py` emits transient chunks; Task Server converts them to official `event_start` / `event_delta`; Nginx disables buffering on Session SSE | `anthropic-smoke-agentkit-0023.json` and the 40-way result both require a non-empty delta before each final message | Passed |
| 2. Visible running state and real interruption | canonical running/idle events, frontend reducer/status UI, `user.interrupt` client action, Task Server work stop, Runtime interrupt-aware cleanup | `anthropic-interrupt-agentkit-0023.json`: tool use observed, interrupt-to-idle about 3.44 seconds, marker absent, physical Session deleted; deployed frontend bundle contains the corresponding state and stop paths | Passed |
| 3. Idle reclaim and five-minute restart | Runtime three-minute idle grace plus lazy physical Session creation for later tool calls | `anthropic-lifecycle-agentkit-0023.json`: first physical ID reclaimed; after 300 seconds, the same logical Session completed another tool turn with a different physical ID | Passed |
| 4. Start sandbox only for tool calls | Task Server `startsSelfHostedSandboxWork()` accepts only `agent.tool_use` | official SDK smoke observed zero sandbox for the pure-text turn, then a correctly bound physical Session for the tool turn | Passed |
| 5. Continuous Markdown record | this file contains implementation, deployment, failed-attempt diagnosis, fixes, image digests, tests, and cleanup state | all seven checklist entries are checked and no credential or signed endpoint literal is present | Passed |
| 6. Official Anthropic Python SDK including a real tool | `anthropic_gateway_e2e.py` uses `anthropic.AsyncAnthropic`; the SDK stream close path is deterministic | final smoke contains streamed pure-text and bash turns, matching tool-use/result IDs, correct physical binding, and empty stderr | Passed |
| 7. Forty concurrent isolated Sessions | shared stream-send gate, unique markers, physical binding sampler, server timestamp overlap calculation; Runtime pacing/retries and physical recovery | final result has 40 logical results, 40 unique simultaneous physical Sessions, maximum bash overlap 40, 40 tool results, all streams active, and zero cross-marker leakage | Passed |

Named deployment targets were also audited directly: dispatcher Runtime
`r-yetovao0sgqgapsacusi`, Tool `t-yetov9c9hce5g253o7oj`, VeADK test code under
`examples/16_self_host_sandbox`, ma-server in its separate checkout, and the
public gateway named above. The deployed Managed Agents JavaScript asset has
SHA-256 `474bcf4b8496ff99b173dbc92bbbec9cb3beef57d9b207e0b3d2175c9eecceeb`,
exactly matching the local production build, and contains the stream,
running/idle, interrupt, and stop-control paths. No requirement remains covered
only by a proxy signal.

## 2026-09-12 - Frontend thinking-block consolidation

The Managed Agents page previously rendered every persisted
`agent.thinking` text fragment as its own conversation entry. Each fragment
had a distinct event and `thinking_id`, so the JSX rendered a separate
`<details>` element with the same `思考过程` summary for every token fragment.
This was a frontend aggregation issue rather than repeated headings emitted by
the model.

`managedConversationEntries()` now keeps one thinking accumulator per user
turn. Stream starts, chunks, stream ends, and canonical thinking fragments all
map to that turn's single entry, including fragments separated by a tool call.
A later `user.message` resets the accumulator and may create one new block for
the next turn. Streamed and canonical text are tracked separately and the
longer representation is displayed, preventing canonical replay from
duplicating already-streamed text. Tool and assistant-message positions remain
unchanged. No CSS or visual pattern was added.

Verification and rollout:

- reducer regression: 3 passed, including distinct canonical IDs, thinking on
  both sides of a tool call, a new user-turn boundary, and streamed/canonical
  reconciliation;
- Managed Agents focused frontend suite: 21 passed;
- full frontend suite with polling watcher: 993 passed;
- TypeScript and Managed Agents Vite production build passed;
- a real previously recorded Session containing 13 persisted thinking fragments
  was replayed through the updated reducer and produced exactly one non-empty
  thinking block;
- Gateway image `20260912-thinking-block-01` was pushed with digest
  `sha256:98edf4524cc86611701818c88dfa56b710d2d4bf2567f12d95c287713001b885`
  and rolled out to both Gateway replicas;
- the public page now references `/assets/app/index--nHwfk8q.js`; its downloaded
  SHA-256 exactly matches the local production build
  (`1eb28a7252a32512cc328f4ccd79c8053602ba46fb297f6995333f93b364de8f`),
  and the public health endpoint returns `ok`.

One diagnostic replay command was initially launched from the repository root
and could not resolve the frontend-local `esbuild` package. It made no changes
and sent no request. Re-running it from `frontend/` completed the real-event
replay described above.
