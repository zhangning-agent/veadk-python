import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(
  new URL("../src/App.tsx", import.meta.url),
  "utf8",
);
const sandboxClientSource = readFileSync(
  new URL("../src/adk/sandbox.ts", import.meta.url),
  "utf8",
);
const dialogSource = readFileSync(
  new URL("../src/ui/SandboxLaunchDialog.tsx", import.meta.url),
  "utf8",
);
const sandboxSessionSource = readFileSync(
  new URL("../src/ui/SandboxSession.tsx", import.meta.url),
  "utf8",
);
const stylesSource = readFileSync(
  new URL("../src/ui/SandboxSession.css", import.meta.url),
  "utf8",
);
const iconSource = readFileSync(
  new URL("../src/ui/icons/InsightIcon.tsx", import.meta.url),
  "utf8",
);
const modeSelectorSource = readFileSync(
  new URL("../src/ui/new-chat-modes/NewChatModeSelector.tsx", import.meta.url),
  "utf8",
);
const openClawWorkspaceSource = readFileSync(
  new URL("../src/ui/OpenClawWorkspace.tsx", import.meta.url),
  "utf8",
);
const hermesWorkspaceSource = readFileSync(
  new URL("../src/ui/HermesWorkspace.tsx", import.meta.url),
  "utf8",
);
const codeWorkspaceSource = readFileSync(
  new URL("../src/ui/CodeSandboxWorkspace.tsx", import.meta.url),
  "utf8",
);
const openClawClientSource = readFileSync(
  new URL("../src/adk/openclaw.ts", import.meta.url),
  "utf8",
);
const hermesClientSource = readFileSync(
  new URL("../src/adk/hermes.ts", import.meta.url),
  "utf8",
);
const codeClientSource = readFileSync(
  new URL("../src/adk/codeSandbox.ts", import.meta.url),
  "utf8",
);
const sandboxBrandSource = readFileSync(
  new URL("../src/ui/SandboxBrandIcon.tsx", import.meta.url),
  "utf8",
);
const workspaceIconsSource = readFileSync(
  new URL("../src/ui/icons/SandboxWorkspaceIcons.tsx", import.meta.url),
  "utf8",
);

test("sandbox access is isolated behind a reusable typed client", () => {
  assert.match(sandboxClientSource, /export interface AgentKitSandboxClient/);
  assert.match(sandboxClientSource, /startSession\(options\?: SandboxRequestOptions\)/);
  assert.match(sandboxClientSource, /sendMessage\([\s\S]*options\?: SandboxRequestOptions/);
  assert.match(sandboxClientSource, /closeSession\([\s\S]*options\?: SandboxRequestOptions/);
  assert.match(sandboxClientSource, /signal\?: AbortSignal/);
  assert.match(sandboxClientSource, /\/web\/sandbox\/sessions/);
  assert.match(sandboxClientSource, /withAuth/);
  assert.match(sandboxClientSource, /withLocalUser/);
  assert.match(sandboxClientSource, /Accept: "text\/event-stream"/);
  assert.match(sandboxClientSource, /onBlocks\?: \(blocks: Block\[\]\) => void/);
  assert.match(sandboxClientSource, /event === "activity"/);
  assert.match(sandboxClientSource, /kind === "thinking"/);
  assert.match(sandboxClientSource, /payload\.kind !== "tool"/);
  assert.match(appSource, /onBlocks: \(blocks\) =>/);
  assert.doesNotMatch(sandboxClientSource, /setTimeout|crypto\.randomUUID/);
});

test("new-chat temporary mode launches the AgentKit sandbox", () => {
  assert.match(modeSelectorSource, /value: "temporary"[\s\S]*?AgentKit 沙箱/);
  assert.match(appSource, /mode === "temporary"[\s\S]*?openSandboxLaunch\(mode\)/);
  assert.doesNotMatch(appSource, /<SandboxEntryButton/);
});

test("sandbox launch dialog covers confirmation loading failure and retry", () => {
  assert.match(dialogSource, /role="dialog"/);
  assert.match(dialogSource, /启用临时会话/);
  assert.match(dialogSource, /将启动 AgentKit 沙箱与 Codex Agent 开启临时会话/);
  assert.match(dialogSource, /您的会话将不会被持久化保存/);
  assert.match(dialogSource, /正在初始化沙箱/);
  assert.match(dialogSource, /启动失败/);
  assert.match(dialogSource, /重新尝试/);
  assert.match(dialogSource, /if \(event\.key === "Escape"/);
  assert.match(appSource, /sandboxLaunchAbortRef\.current\?\.abort\(\)/);
});

test("active sandbox conversation is visibly temporary and never uses normal sessions", () => {
  assert.match(sandboxSessionSource, /当前为临时会话，退出后对话内容消失/);
  assert.match(sandboxSessionSource, /退出临时会话/);
  assert.match(appSource, /sandboxClient\.sendMessage/);
  assert.doesNotMatch(sandboxClientSource, /runSSE|listSessions/);
  assert.match(stylesSource, /\.main\.is-sandbox-session::before/);
  assert.match(stylesSource, /\.sandbox-session-warning/);
  assert.match(
    stylesSource,
    /\.sandbox-session-warning-copy[\s\S]*text-align:\s*center/,
  );
  assert.match(
    stylesSource,
    /\.sandbox-composer-wrap \.composer-box[\s\S]*grid-template-rows/,
  );
  assert.match(
    stylesSource,
    /\.main\.is-sandbox-session[\s\S]*linear-gradient\([\s\S]*to bottom/,
  );
});

test("sandbox visuals use repository-owned icons and reduced motion", () => {
  assert.match(iconSource, /export function InsightIcon/);
  assert.match(iconSource, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(iconSource, /lucide-react|<img|data:image/);
  assert.match(stylesSource, /@media \(prefers-reduced-motion: reduce\)/);
});

test("OpenClaw, Hermes, and Code sandboxes use repository-owned brand marks", () => {
  assert.match(sandboxBrandSource, /assets\/openclaw\.svg/);
  assert.match(sandboxBrandSource, /assets\/hermes\.svg/);
  assert.match(
    modeSelectorSource,
    /brand=\{mode === "code-sandbox" \? "code" : mode\}/,
  );
  assert.match(openClawWorkspaceSource, /<SandboxBrandIcon brand="openclaw"/);
  assert.match(hermesWorkspaceSource, /<SandboxBrandIcon brand="hermes"/);
  assert.match(codeWorkspaceSource, /<SandboxBrandIcon brand="code"/);
  assert.match(
    sandboxBrandSource,
    /M2\.9 25\.8 8\.3 9\.9a1 1 0 0 1 1\.9 0l5\.3 15\.9H2\.9Z/,
  );
  assert.doesNotMatch(sandboxBrandSource, /https?:\/\//);
});

test("sandbox workspaces use repository-owned accessible controls", () => {
  assert.doesNotMatch(openClawWorkspaceSource, /lucide-react/);
  assert.doesNotMatch(hermesWorkspaceSource, /lucide-react/);
  assert.doesNotMatch(codeWorkspaceSource, /lucide-react/);
  assert.doesNotMatch(workspaceIconsSource, /lucide-react|<img|https?:\/\//);
  assert.match(workspaceIconsSource, /stroke="currentColor"/);
  assert.match(workspaceIconsSource, /aria-hidden="true"/);
  assert.match(workspaceIconsSource, /export function SandboxTerminalIcon/);
  assert.match(openClawWorkspaceSource, /event\.key !== "ArrowLeft"/);
  assert.match(hermesWorkspaceSource, /event\.key !== "ArrowLeft"/);
  assert.match(codeWorkspaceSource, /event\.key !== "ArrowLeft"/);
  assert.match(openClawWorkspaceSource, /tabIndex=\{view === "webui" \? 0 : -1\}/);
  assert.match(hermesWorkspaceSource, /tabIndex=\{view === "terminal" \? 0 : -1\}/);
  assert.match(codeWorkspaceSource, /tabIndex=\{view === "webui" \? 0 : -1\}/);
});

test("sandbox lifecycle cards show brand identity instead of internal ids", () => {
  assert.match(
    openClawWorkspaceSource,
    /openclaw-lifecycle__brand[\s\S]*<SandboxBrandIcon[\s\S]*brand="openclaw"/,
  );
  assert.match(
    hermesWorkspaceSource,
    /hermes-lifecycle__brand[\s\S]*<SandboxBrandIcon[\s\S]*brand="hermes"/,
  );
  assert.match(
    codeWorkspaceSource,
    /code-sandbox-lifecycle__brand[\s\S]*<SandboxBrandIcon[\s\S]*brand="code"/,
  );
  assert.doesNotMatch(openClawWorkspaceSource, /ID \{session\.sandboxId\}|复制沙箱 ID/);
  assert.doesNotMatch(hermesWorkspaceSource, /ID \{session\.sandboxId\}|复制沙箱 ID/);
  assert.doesNotMatch(codeWorkspaceSource, /ID \{session\.sandboxId\}|复制沙箱 ID/);
});

test("all three branded sandboxes are grouped under the Agent sandbox submenu", () => {
  assert.match(modeSelectorSource, />Agent 沙箱</);
  assert.match(modeSelectorSource, /aria-label="Agent 沙箱"/);
  assert.match(modeSelectorSource, /role="menuitem"/);
  assert.match(modeSelectorSource, /role="menuitemradio"/);
  assert.match(modeSelectorSource, /value: "code-sandbox"/);
  assert.match(modeSelectorSource, /TOP_LEVEL_ENTRIES[\s\S]*kind: "sandbox"/);
  assert.match(modeSelectorSource, /event\.key === "ArrowRight"/);
  assert.match(
    modeSelectorSource,
    /SANDBOX_MODES\.map\(\(sandboxMode\)[\s\S]*choose\(sandboxMode\)/,
  );
});

test("branded sandboxes default to WebUI and expose a Terminal tab", () => {
  for (const source of [openClawWorkspaceSource, hermesWorkspaceSource]) {
    assert.match(source, /useState<"webui" \| "terminal">\("webui"\)/);
    assert.match(source, /role="tablist"/);
    assert.match(source, />\s*WebUI\s*</);
    assert.match(source, />\s*Terminal\s*</);
    assert.match(source, /session\.webuiUrl/);
    assert.match(source, /session\.terminalUrl/);
  }
  assert.match(codeWorkspaceSource, /useState<"webui" \| "terminal">\("webui"\)/);
  assert.match(codeWorkspaceSource, /role="tablist"/);
  assert.match(codeWorkspaceSource, />\s*Codex\s*</);
  assert.match(codeWorkspaceSource, />\s*Terminal\s*</);
  assert.match(codeWorkspaceSource, /session\.webuiUrl/);
  assert.match(codeWorkspaceSource, /session\.terminalUrl/);
  assert.match(openClawClientSource, /webuiUrl: string/);
  assert.match(openClawClientSource, /terminalUrl: string/);
  assert.match(hermesClientSource, /webuiUrl: string/);
  assert.match(hermesClientSource, /terminalUrl: string/);
  assert.match(codeClientSource, /webuiUrl: string/);
  assert.match(codeClientSource, /terminalUrl: string/);
});

test("branded sandboxes minimize without destroying and can reopen", () => {
  assert.match(openClawWorkspaceSource, /aria-label="最小化 OpenClaw"/);
  assert.match(openClawWorkspaceSource, /aria-label="打开 OpenClaw"/);
  assert.match(hermesWorkspaceSource, /aria-label="最小化 Hermes"/);
  assert.match(hermesWorkspaceSource, /aria-label="打开 Hermes"/);
  assert.match(codeWorkspaceSource, /aria-label="最小化 Code 沙箱"/);
  assert.match(codeWorkspaceSource, /aria-label="打开 Code 沙箱"/);
  assert.match(appSource, /function minimizeOpenClawSession\(\)/);
  assert.match(appSource, /function minimizeHermesSession\(\)/);
  assert.match(appSource, /function minimizeCodeSandboxSession\(\)/);
  assert.match(
    appSource,
    /onMinimize=\{minimizeOpenClawSession\}[\s\S]*onExit=\{exitOpenClawSession\}/,
  );
  assert.match(
    appSource,
    /onMinimize=\{minimizeHermesSession\}[\s\S]*onExit=\{exitHermesSession\}/,
  );
  assert.match(
    appSource,
    /onMinimize=\{minimizeCodeSandboxSession\}[\s\S]*onExit=\{exitCodeSandboxSession\}/,
  );
});

test("branded sandbox sessions can coexist and switch independently", () => {
  assert.match(
    appSource,
    /function openSandboxLaunch\(\s*mode: "temporary" \| "openclaw" \| "hermes" \| "code-sandbox"/,
  );
  assert.match(
    appSource,
    /mode === "openclaw" && hermesSession[\s\S]*setHermesMinimized\(true\)/,
  );
  assert.match(
    appSource,
    /mode === "hermes" && openClawSession[\s\S]*setOpenClawMinimized\(true\)/,
  );
  assert.match(
    appSource,
    /mode === "code-sandbox" && openClawSession[\s\S]*setOpenClawMinimized\(true\)[\s\S]*mode === "code-sandbox" && hermesSession[\s\S]*setHermesMinimized\(true\)/,
  );

  const openClawLaunch = appSource.slice(
    appSource.indexOf("if (launchingOpenClaw)"),
    appSource.indexOf("} else if (launchingHermes)"),
  );
  const hermesLaunch = appSource.slice(
    appSource.indexOf("} else if (launchingHermes)"),
    appSource.indexOf("} else if (launchingCodeSandbox)"),
  );
  const codeLaunch = appSource.slice(
    appSource.indexOf("} else if (launchingCodeSandbox)"),
    appSource.indexOf(
      "} else {",
      appSource.indexOf("} else if (launchingCodeSandbox)"),
    ),
  );
  assert.doesNotMatch(openClawLaunch, /setHermesSession\(null\)/);
  assert.doesNotMatch(openClawLaunch, /setCodeSandboxSession\(null\)/);
  assert.doesNotMatch(hermesLaunch, /setOpenClawSession\(null\)/);
  assert.doesNotMatch(hermesLaunch, /setCodeSandboxSession\(null\)/);
  assert.doesNotMatch(codeLaunch, /setOpenClawSession\(null\)/);
  assert.doesNotMatch(codeLaunch, /setHermesSession\(null\)/);
});
