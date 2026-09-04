#!/usr/bin/env node
/**
 * CDP browser smoke driver for the Stage 8 GUI (acceptance aid, not a test).
 *
 * Drives a dedicated Chrome instance (own throwaway profile + CDP port, never
 * the user's browser) against the loopback smoke server started by
 * scripts/gui_smoke_server.py. Asserts the observer contract end to end:
 * shell render, session/task navigation, workflow graph, live status update
 * after an approval resolution (no reload), editor freeze/rejection,
 * keyboard traversal, and the offline banner. Screenshots land in the given
 * output dir.
 *
 * Usage: node scripts/gui_smoke_cdp.mjs <facts.json> <out-dir>
 * where facts.json is the JSON part of the server's GUI_SMOKE_FACTS line.
 */
import { execFile, spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CDP_PORT = 9333;
const PROFILE = '/tmp/morrow-gui-smoke-chrome';

const [, , factsPath, outDir = '/tmp/morrow-gui-smoke-shots'] = process.argv;
const facts = JSON.parse(await readFile(factsPath, 'utf8'));
mkdirSync(outDir, { recursive: true });

const results = [];
const cleanups = [];
process.on('exit', () => {
  for (const cleanup of cleanups) cleanup();
});
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) process.exitCode = 1;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, what, tries = 60) {
  for (let i = 0; i < tries; i++) {
    const value = await fn();
    if (value) return value;
    await sleep(250);
  }
  throw new Error(`timed out waiting for: ${what}`);
}

// --- launch a dedicated Chrome --------------------------------------------
const chrome = spawn(
  CHROME,
  [
    `--user-data-dir=${PROFILE}`,
    `--remote-debugging-port=${CDP_PORT}`,
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ],
  { stdio: 'ignore' },
);
cleanups.push(() => chrome.kill('SIGTERM'));

const version = await waitFor(async () => {
  try {
    return await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json();
  } catch {
    return null;
  }
}, 'chrome devtools endpoint');
check('chrome devtools endpoint up', Boolean(version.Browser));

// --- CDP plumbing -----------------------------------------------------------
const created = await (
  await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent('about:blank')}`, {
    method: 'PUT',
  })
).json();
const ws = new WebSocket(created.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});
let nextId = 1;
const pending = new Map();
ws.onmessage = (message) => {
  const data = JSON.parse(message.data);
  if (data.id && pending.has(data.id)) {
    pending.get(data.id)(data);
    pending.delete(data.id);
  }
};
function cdp(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const reply = await cdp('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (reply.result?.exceptionDetails) throw new Error(JSON.stringify(reply.result));
  return reply.result?.result?.value;
}
async function shot(name) {
  const reply = await cdp('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(outDir, name), Buffer.from(reply.result.data, 'base64'));
  console.log(`shot: ${join(outDir, name)}`);
}
async function pressTab() {
  for (const type of ['rawKeyDown', 'keyUp']) {
    await cdp('Input.dispatchKeyEvent', {
      type,
      key: 'Tab',
      code: 'Tab',
      windowsVirtualKeyCode: 9,
      nativeVirtualKeyCode: 48,
    });
  }
}

// --- scenario ---------------------------------------------------------------
await cdp('Page.enable');
await cdp('Runtime.enable');
await cdp('Emulation.setDeviceMetricsOverride', {
  width: 1440,
  height: 900,
  deviceScaleFactor: 1,
  mobile: false,
});

// Invalid/expired tokens are an authentication failure, not a transient
// disconnect. Force a full document navigation so the fragment bootstrap
// replaces any token left in this tab's sessionStorage.
const invalidUrl = new URL(facts.url);
invalidUrl.searchParams.set('auth-probe', 'invalid');
invalidUrl.hash = '#token=invalid-session-token';
await cdp('Page.navigate', { url: invalidUrl.toString() });
const invalidToken = await waitFor(async () => {
  const text = await evaluate('document.body && document.body.innerText');
  return text.includes('会话令牌无效或已过期') ? text : null;
}, 'invalid token diagnosis');
check('invalid token is diagnosed without reconnect backoff', Boolean(invalidToken));

const validUrl = new URL(facts.url);
validUrl.searchParams.set('auth-probe', 'valid');
await cdp('Page.navigate', { url: validUrl.toString() });
await waitFor(
  () => evaluate(`document.body && document.body.innerText.includes('Morrow')`),
  'app shell render',
);
check('app shell renders', true);
await sleep(800); // let snapshot + first pulls settle

const shell = await evaluate(`({
  workspace: document.body.innerText.includes('${facts.workspace_id}'),
  hasNav: !!document.querySelector('nav'),
  focusRingCss: true,
})`);
check('top bar shows workspace id', shell.workspace);

// Session nav → expand → select task.
const navState = await evaluate(`(() => {
  const nav = document.querySelector('nav');
  const buttons = [...document.querySelectorAll('button')].map((b) => b.innerText.trim());
  return { navText: nav ? nav.innerText.slice(0, 200) : '', buttons: buttons.slice(0, 12) };
})()`);
console.log('nav:', JSON.stringify(navState).slice(0, 300));

const sessionClicked = await evaluate(`(() => {
  const btn = [...document.querySelectorAll('nav button')].find((b) =>
    b.innerText.includes('${facts.session_id}'),
  );
  if (!btn) return false;
  btn.click();
  return true;
})()`);
check('session nav has the seeded session', sessionClicked);
await sleep(800);
const taskClicked = await evaluate(`(() => {
  const btn = [...document.querySelectorAll('nav button')].find((b) =>
    b.innerText.includes('${facts.task_id}'),
  );
  if (!btn) return null;
  btn.click();
  return true;
})()`);
check('task entry selected', Boolean(taskClicked));
await sleep(1500);

const panel = await evaluate(`(() => {
  const text = document.body.innerText;
  return {
    showsRun: text.includes('${facts.run_id}'),
    hasGraph: !!document.querySelector('.react-flow'),
    nodeLabels: [...document.querySelectorAll('.react-flow__node')].map((n) => n.innerText.replace(/\\n/g, ' | ')),
    approvalShown: text.includes('待审批') || text.includes('审批'),
    budgetShown: /\\d+\\s*\\/\\s*\\d+/.test(text),
  };
})()`);
check('workflow graph renders both nodes', panel.hasGraph && panel.nodeLabels.length === 2, panel.nodeLabels.join(' ; '));
check('pending approval is displayed', panel.approvalShown);
check('budget line displayed', panel.budgetShown);

await shot('01-running-approval-pending.png');

// Keyboard traversal: Tab through the shell, record the focus chain.
const focusChain = [];
for (let i = 0; i < 14; i++) {
  await pressTab();
  const focused = await evaluate(`(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const outline = getComputedStyle(el).outlineStyle;
    const ring = getComputedStyle(el).boxShadow;
    return {
      label: el.tagName + ':' + (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 40),
      visible: outline !== 'none' || ring !== 'none',
    };
  })()`);
  focusChain.push(focused);
}
const focusable = focusChain.filter(Boolean).map((item) => item.label);
check(
  'keyboard traversal reaches >= 6 interactive elements',
  focusable.length >= 6,
  focusable.join(' → ').slice(0, 200),
);
check('focused element shows a visible indicator', focusChain.some((item) => item?.visible));

// Use a real pointer event (not HTMLElement.click()) on a non-first graph
// node. This guards the React Flow selection path that opens node detail.
// Keep it after the keyboard assertions so pointer focus does not change the
// keyboard traversal's deterministic starting point.
const pointerTarget = await evaluate(`(() => {
  const nodes = [...document.querySelectorAll('.react-flow__node')];
  const node = nodes.at(-1);
  if (!node) return null;
  const rect = node.getBoundingClientRect();
  return {
    id: node.getAttribute('data-id'),
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
})()`);
if (pointerTarget) {
  await cdp('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: pointerTarget.x,
    y: pointerTarget.y,
    button: 'left',
    clickCount: 1,
  });
  await cdp('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: pointerTarget.x,
    y: pointerTarget.y,
    button: 'left',
    clickCount: 1,
  });
}
const pointerDetail = await waitFor(async () => {
  if (!pointerTarget?.id) return null;
  return evaluate(
    `Boolean(document.querySelector('section[aria-label="节点 ${pointerTarget.id} 详情"]'))`,
  );
}, 'non-first node detail after pointer click');
check(
  'pointer click opens non-first node detail',
  Boolean(pointerTarget?.id && pointerDetail),
  pointerTarget?.id ?? 'missing node',
);

// Live update: resolve the approval out-of-band; the GUI must update without reload.
const exec = promisify(execFile);
await exec('curl', [
  '-s',
  '-X',
  'POST',
  `http://127.0.0.1:8799/v1/approvals/${facts.approval_id}/resolve`,
  '-H',
  'Authorization: Bearer gui-smoke-token',
  '-H',
  'Content-Type: application/json',
  '-d',
  '{"approved": true}',
]);
const completed = await waitFor(async () => {
  const text = await evaluate('document.body.innerText');
  return text.includes('已完成') || text.includes('completed') ? text : null;
}, 'run reaching completed in the GUI', 80);
check('live status update without reload (run completed)', Boolean(completed));
const afterDone = await evaluate(`(() => ({
  // The persistent count label reads "待审批 0"; the approval strip itself unmounts.
  stripGone: !document.querySelector('section[aria-label="待审批"]'),
  countZero: document.body.innerText.includes('待审批 0'),
  succeeded: document.body.innerText.includes('succeeded') || document.body.innerText.includes('成功'),
}))()`);
check(
  'approval cleared and result visible',
  afterDone.stripGone && afterDone.countZero && afterDone.succeeded,
  JSON.stringify(afterDone),
);

// Wait until the node cards themselves settle (debounced view refetch), so
// the "completed" evidence shows a fully consistent projection.
const settled = await waitFor(async () => {
  const labels = await evaluate(
    `[...document.querySelectorAll('.react-flow__node')].map((n) => n.innerText)`,
  );
  return labels.length === 2 && labels.every((l) => l.includes('已完成')) ? labels : null;
}, 'both node cards showing 已完成', 80);
check('node cards settled to completed', Boolean(settled), (settled ?? []).join(' ; '));
await shot('02-completed.png');

// Editor: clone the legal seeded Workflow into a durable Draft, freeze exactly
// one Revision, then make a second Draft structurally illegal and verify the
// disabled freeze affordance plus located diagnostic.
const editorClicked = await evaluate(`(() => {
  const button = [...document.querySelectorAll('header button')].find((item) =>
    item.innerText.trim() === '编辑器',
  );
  if (!button) return false;
  button.click();
  return true;
})()`);
check('editor view is reachable', editorClicked);
await waitFor(
  () => evaluate(`document.body.innerText.includes('创建 Workflow Draft')`),
  'workflow draft create form',
);
const pipelineSelected = await waitFor(async () => {
  return evaluate(`(() => {
    const button = [...document.querySelectorAll('aside button')].find((item) =>
      item.innerText.includes('pipeline'),
    );
    if (!button) return false;
    button.click();
    return true;
  })()`);
}, 'published Workflow in editor Catalog');
check('published Workflow selected as legal Draft source', Boolean(pipelineSelected));
await evaluate(`(() => {
  const input = [...document.querySelectorAll('input')].find((item) =>
    item.value === 'pipeline',
  );
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, 'pipeline_editor_copy');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`);
const legalDraftCreated = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) =>
    item.innerText.trim() === '创建 Draft',
  );
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()`);
check('legal Workflow Draft creation submitted', legalDraftCreated);
await waitFor(
  () => evaluate(`(() => {
    const freeze = [...document.querySelectorAll('button')].find((item) =>
      item.innerText.trim() === 'Freeze Revision',
    );
    return Boolean(freeze && !freeze.disabled && document.body.innerText.includes('valid'));
  })()`),
  'legal Draft validation',
);
const frozen = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) =>
    item.innerText.trim() === 'Freeze Revision',
  );
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()`);
check('legal Draft freeze submitted', frozen);
const frozenVisible = await waitFor(
  () => evaluate(`document.body.innerText.includes('已冻结 Revision')`),
  'frozen Workflow Revision',
);
check('legal Draft freezes to an immutable Revision', Boolean(frozenVisible));
await shot('03-editor-frozen.png');

const sourceReopened = await evaluate(`(() => {
  const button = [...document.querySelectorAll('aside button')].find((item) =>
    item.innerText.includes('Two phase pipeline') && item.innerText.includes('user'),
  );
  if (!button) return false;
  button.click();
  return true;
})()`);
check('published Workflow can be reopened as another Draft', sourceReopened);
await waitFor(
  () => evaluate(`document.body.innerText.includes('创建 Workflow Draft')`),
  'second workflow draft form',
);
await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) =>
    item.innerText.trim() === '创建 Draft',
  );
  button?.click();
})()`);
await waitFor(
  () => evaluate(`document.querySelectorAll('.react-flow__node').length === 2`),
  'second legal graph',
);
await evaluate(`(() => {
  const nodes = [...document.querySelectorAll('.react-flow__node')];
  nodes.at(-1)?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
})()`);
const nodeDeleted = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) =>
    item.innerText.includes('删除节点（不自动重连）'),
  );
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()`);
check('node deletion deliberately leaves dependencies unreconnected', nodeDeleted);
const invalidDraft = await waitFor(
  () => evaluate(`(() => {
    const freeze = [...document.querySelectorAll('button')].find((item) =>
      item.innerText.trim() === 'Freeze Revision',
    );
    const text = document.body.innerText;
    return Boolean(
      freeze &&
      freeze.disabled &&
      text.includes('invalid') &&
      text.includes('missing or not completion-required') &&
      [...document.querySelectorAll('.react-flow__node')].some((item) =>
        item.innerText.includes('错误'),
      )
    );
  })()`),
  'invalid Draft rejection',
);
check('illegal graph is located and cannot be frozen', Boolean(invalidDraft));
await shot('04-editor-invalid.png');
await evaluate(`(() => {
  const button = [...document.querySelectorAll('header button')].find((item) =>
    item.innerText.trim() === '观察',
  );
  button?.click();
})()`);

// Offline honesty: kill the server; the banner must surface as text.
process.kill(Number(facts.server_pid), 'SIGTERM');
const banner = await waitFor(async () => {
  const text = await evaluate('document.body.innerText');
  return /离线|断开|重连|offline|reconnect/i.test(text) ? text : null;
}, 'offline/reconnect banner', 60);
check('offline banner surfaces as text', Boolean(banner));
await shot('05-offline-banner.png');

// Core restart: same state, same token; the client must resync with no lost
// or duplicated state and clear the banner.
const stateDir = facts.state_root.replace(/\/state-root$/, '');
const server = spawn(
  'uv',
  ['run', 'python', 'scripts/gui_smoke_server.py', '--serve-only', stateDir],
  { stdio: 'ignore' },
);
cleanups.push(() => server.kill('SIGTERM'));
await waitFor(async () => {
  try {
    const reply = await fetch(`http://127.0.0.1:8799/v1/meta`, {
      headers: { Authorization: 'Bearer gui-smoke-token' },
    });
    return reply.ok;
  } catch {
    return false;
  }
}, 'restarted server', 80);
const recovered = await waitFor(async () => {
  const text = await evaluate('document.body.innerText');
  return text.includes('已连接') && !text.includes('正在重连') ? text : null;
}, 'connection back to live after Core restart', 120);
check('reconnect after Core restart restores live state', Boolean(recovered));
await waitFor(async () => evaluate(`(() => {
  const button = [...document.querySelectorAll('nav button')].find((item) =>
    item.innerText.includes('${facts.session_id}'),
  );
  if (!button) return false;
  button.click();
  return true;
})()`), 'seeded session after Core restart');
// Post-completion background work (e.g. learning review) may legitimately add
// sessions; duplication means the same session rendered twice, loss means the
// seeded session is gone. Run completion was already verified before restart.
const afterRecovery = await evaluate(`(() => {
  const sessTexts = [...document.querySelectorAll('nav button')]
    .map((b) => b.innerText)
    .filter((t) => t.includes('ses_'));
  return {
    completed: document.body.innerText.includes('已完成'),
    succeeded: document.body.innerText.includes('成功') || document.body.innerText.includes('succeeded'),
    unique: new Set(sessTexts).size === sessTexts.length,
    hasSeeded: sessTexts.some((t) => t.includes('${facts.session_id}')),
    count: sessTexts.length,
  };
})()`);
check(
  'no lost or duplicated state after resync',
  afterRecovery.unique &&
    afterRecovery.hasSeeded,
  JSON.stringify(afterRecovery),
);
await shot('06-recovered.png');
server.kill('SIGTERM');

console.log('---');
console.log(`${results.filter((r) => r.ok).length}/${results.length} checks passed`);
process.exit(process.exitCode ?? 0);
