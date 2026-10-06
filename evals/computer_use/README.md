# Controlled native computer-use gates

`native_counter.py` is a separate opt-in component gate for exactly one
background or foreground token click on the running fixture's Increment button:

```sh
/tmp/morrow-computer-use-sdk-check/bin/python evals/computer_use/native_counter.py \
  --allow-desktop --allow-one-increment \
  --fixture-bundle-id com.morrow.ComputerUseFixture \
  --fixture-state-file /tmp/morrow-computer-use-scroll-evidence/state.json \
  --delivery background
```

It binds the independent fixture PID/window/instance, rejects concurrent counter
changes before SDK admission, consumes the observation once, reads a fresh SDK
snapshot and normalized capture, and checks the independent count. It never retries
or falls back to another delivery mode. An independently observed increment
does not upgrade the SDK's unknown outcome: the gate fails unless the normalized
native outcome is completed. The pinned macOS SDK reports generic AX presses as
unverifiable because dispatch success has no independent read-back. Record any
actual effect even when this gate exits nonzero. This does not attest text input,
whole-tree uniqueness, Provider hydration, or full production acceptance.

These are explicit opt-in gates, excluded from default pytest. They operate on
an isolated local fixture with no network, account, or real credential. SDK
installation is separate from desktop authorization. Permission probes are
read-only; the gate does not request TCC permission or create a daemon.

Build without launching:

```sh
uv run python evals/computer_use/build_fixture.py --output-directory /tmp/morrow-computer-use-fixture
UV_PROJECT_ENVIRONMENT=/tmp/morrow-computer-use-sdk-check uv sync --locked --extra computer-use
```

After the user authorizes native fixture access, launch the built app locally:

```sh
open /tmp/morrow-computer-use-fixture/MorrowComputerUseFixture.app
/tmp/morrow-computer-use-sdk-check/bin/python evals/computer_use/native_readonly.py \
  --allow-desktop --fixture-bundle-id com.morrow.ComputerUseFixture
```

The script accepts only this fixture identity and requires exactly one selected
window. With multiple SDK surfaces, supply `--fixture-state-file` pointing to
the running fixture's independent `state.json`; selection matches both its PID
and native window number in the trusted adapter registry. Without that file,
multiple candidates remain an error. This acceptance-only selector is not a
model-facing grant or a title-based fallback.

It creates a named Session, discovers the fixture, reads its AX tree and
screenshot, checks encoded dimensions by decoding, then ends the Session and
shuts down the Driver. It prints bounded metadata, counts, dimensions and hash;
SDK objects, AX text, image bytes, input values and native exceptions are absent
from its output. A capture stays transient and is never sent to a Provider.

Add `--core-owner` to run construction, discovery, observation and shutdown on
the actual CoreHost owner loop. Its minimal host context proves owner-thread
compatibility but does not attest full GUI composition or responsible-host
authorization. `owner_main_thread` records the actual thread. Both modes report
tree truncation and image-sharing rejection separately: passing capture/decode
can still have bounded decoding/geometry image errors. Neither mode opens
production gates.

The adapter bounds SDK traversal at 400 nodes, while model output remains capped
at 200 elements, depth 8 and 32 KiB of text. The pinned SDK counts collapsed
layout containers against traversal; those are not exported elements. Native truncation or projection omission affects absence/uniqueness claims,
but does not block a geometrically valid screenshot. No content masking or classification runs. For bounded
acceptance comparisons only, `--native-walk-limit 200` or `400` overrides the
SDK traversal request, leaving production and model projection limits unchanged.

Record the actual responsible host, OS, CPU, Python/SDK versions and TCC facts.
A CLI result does not prove GUI responsible-host authorization or real model
quality. The app builder uses the SwiftUI property wrapper via a type alias so
it can build on SDKs where the Command Line Tools omit SwiftUI macro plugins.

A passing read-only gate does not authorize keyboard/mouse effects. The later
C06/C11 action gates must receive explicit controlled-fixture authorization and
verify the fixture's own state. Production desktop activation remains closed
until native acceptance and the remaining tool/image/permission wiring pass.

The desktop lease conservatively serializes all login desktops of the same uid.
It is shared across workspaces and CLI processes, and retained across model and
approval waits. Lock files remain empty and are never unlinked. Cancellation or
timeout stops admission and quarantines the Session while native work settles;
it does not cancel or repeat a native action. A Host with unsettled desktop
shutdown retains its owner loop rather than forcing it to stop.

## Offline installed-package gate

Build the GUI, sdist and wheel first. Use separate disposable environments so
the no-extra check really has no SDK, then replace the editable project with
the wheel. The commands below use the current package version from pyproject.

```sh
uv run python scripts/build_release.py
package_gate_root=$(mktemp -d -t morrow-package-gate)
UV_PROJECT_ENVIRONMENT="$package_gate_root/no-extra" uv sync --locked --no-default-groups --python 3.12
UV_PROJECT_ENVIRONMENT="$package_gate_root/extra" uv sync --locked --no-default-groups --python 3.12 --extra computer-use
uv pip install --python "$package_gate_root/no-extra/bin/python" --no-deps --reinstall dist/morrow_agent-0.1.0-py3-none-any.whl
uv pip install --python "$package_gate_root/extra/bin/python" --no-deps --reinstall dist/morrow_agent-0.1.0-py3-none-any.whl
"$package_gate_root/no-extra/bin/python" -I evals/computer_use/package_smoke.py \
  --expect-sdk absent --require-wheel --gui-source src/morrow/gui_static \
  --wheel dist/morrow_agent-0.1.0-py3-none-any.whl --sdist dist/morrow_agent-0.1.0.tar.gz
"$package_gate_root/extra/bin/python" -I evals/computer_use/package_smoke.py \
  --expect-sdk present --sdk-version 0.30.4+morrow.3 --require-wheel --gui-source src/morrow/gui_static \
  --wheel dist/morrow_agent-0.1.0-py3-none-any.whl --sdist dist/morrow_agent-0.1.0.tar.gz
"$package_gate_root/no-extra/bin/morrow" --help
"$package_gate_root/extra/bin/morrow" --help
uv pip check --python "$package_gate_root/no-extra/bin/python"
uv pip check --python "$package_gate_root/extra/bin/python"
```

The verifier compares every installed/archived GUI file with the current build,
checks SDK absence or its pinned version, and runs the production ordinary
AgentLoop with a scripted Provider and an actual read of a disposable file.
It rejects SDK imports and Internet socket connections during that task, checks
the real completed turn and tool result, and confirms the desktop owner stayed
inactive. Output contains only fixed check codes, versions, counts and hashes.
This proves packaging and default-off behavior; it does not prove native device
access or model quality. No desktop authorization is needed for this gate.
On macOS arm64 the verifier expects the functional SDK version `0.30.4+morrow.3`;
other hosts install official 0.30.4 and require the explicit `--sdk-version 0.30.4`.

## Independent fixture state

Build a separate app bundle with an explicit private state directory. Building
does not launch the app, activate the SDK, or request system permissions:

```sh
uv run python evals/computer_use/build_fixture.py \
  --output-directory /tmp/morrow-computer-use-state-fixture \
  --state-directory /tmp/morrow-computer-use-state-evidence
```

Once that exact app is running, `state.json` is its independent effect oracle.
The snapshot records instance UUID, PID, revision, counter, controlled Unicode
text, scroll offset, native window number/frame/backing scale, and whether the
synthetic secure field is populated. Schema v2 also exports live ordinary/secure
synthetic strings, field identity and native event counts. Only Enter commits
the text binding; losing focus preserves live input without counting as commit.
The window frame uses AppKit screen points; it is not an SDK screenshot pixel
frame. Verify the snapshot PID/window belongs to the selected fixture instance.
The app displays `State output: ready`, `failed`, or `disabled`; a failed writer
is not evidence of a successful action. Without `--state-directory` no state is
written. Use only synthetic input in this application, never actual credentials.
On macOS 15+, scroll export uses SwiftUI's scroll geometry callback. The legacy
preference fallback is retained for older systems; verify it on those systems
before claiming a working scroll oracle there.

The browser fixture binds only to 127.0.0.1 and launches no browser or SDK:

```sh
uv run python evals/computer_use/browser_fixture.py \
  --state-directory /tmp/morrow-controlled-browser-state --port 18788
```

Open the printed localhost URL in an isolated test tab. The form has its own
counter, Unicode echo, password field and scroll rows. It exports independent
atomic `state.json` snapshots with instance/PID/revision, and keeps secure input
entirely in the page; only a Boolean populated marker is transmitted. Scroll
offset is in CSS pixels. Events require the exact loopback Host and same Origin,
bounded JSON and known fields; there are no external resources or accounts.
Use fresh state directories for each campaign; startup creates a new instance
and resets state. These fixtures provide evidence inputs for native acceptance;
building them or driving the form through Browser Use does not prove Morrow's
SDK can observe or operate the native desktop.

## Full GUI responsible-host read probe

After building the GUI and running the independent Swift fixture, this explicit
probe uses production application/bootstrap, workspace/runtime registry,
CoreHost, full chat/workflow service context, and ASGI GUI composition:

```sh
/tmp/morrow-computer-use-sdk-check/bin/python evals/computer_use/gui_readonly.py \
  --allow-desktop --fixture-bundle-id com.morrow.ComputerUseFixture \
  --fixture-state-file /tmp/morrow-computer-use-scroll-evidence/state.json \
  --evidence-file /tmp/morrow-native-gui-read.json
```

It allocates its own temporary workspace/state root and memory-only synthetic
Provider credential. The scripted Provider makes no model/network request.
The SDK read/decode/normalization/close runs on that full host's owner loop before its
local GUI serves. Output includes only bounded evidence and the actual loopback
URL. A held OS-assigned socket prevents conflicts and port-selection races.
Open that URL in an isolated test tab; normal scripted chat can verify that the
GUI composition still works after the SDK probe. Stop the process to close its
host/listener and remove its temporary state.

The host's ordinary computer-use lifecycle remains not_activated;
the probe does not create an active desktop run. This observes the fixed fixture via the opt-in
probe, not through a model grant. Invalid capture geometry/bytes, minimal
service context or active production desktop fails the
probe; it never opens a diagnostic SDK endpoint to HTTP clients. This verifies
responsible-host/read coexistence, not the native action/Provider-image/product
end-to-end acceptance gates.

## Basic native input and ordinary loop

Install the `computer-use` optional extra in a disposable environment. On macOS
arm64 it selects the reproducible functional SDK; its exact upstream source and
patch are tracked under `vendor/cua-driver-functional`. No security-classification
query or field-specific input guard is needed. Password
fields use the same authorized keyboard path as ordinary fields. Public SDK
values and screenshot pixels receive no tool-side content classification or masking.

```sh
/path/to/isolated/python evals/computer_use/native_text.py \
  --allow-desktop --allow-one-text-insert \
  --fixture-bundle-id com.morrow.ComputerUseFixture \
  --fixture-state-file /tmp/morrow-computer-use-scroll-evidence/state.json \
  --evidence-file /tmp/morrow-native-text.json
```

The harness inserts one synthetic Unicode marker and compares an independent
fixture state. `--action press_key --allow-one-key` and
`--action hotkey --allow-one-key` select fixed keys; `--keyboard-marker-set release`
selects unused q/Shift+Y markers. An existing marker refuses a duplicate run.
Unknown remains unknown even when the fixture records a change; no input is retried.

`native_loop.py` uses the ordinary AgentLoop, ScriptedProvider, local fixture grant,
manual approval, durable ledger and before/after Artifact image hashes. It uses
no real account or Provider network:

```sh
/path/to/isolated/python evals/computer_use/native_loop.py \
  --allow-desktop --allow-one-text-insert \
  --fixture-bundle-id com.morrow.ComputerUseFixture \
  --fixture-state-file /tmp/morrow-computer-use-scroll-evidence/state.json \
  --evidence-file /tmp/morrow-native-loop.json
```

The previous guarded SDK prototype, custom build route and refusal experiments
are retired by the 2026-10-03 user scope correction. Historical evidence remains
in Git/local acceptance archives and is not a current implementation requirement.

`native_text.py --field secure --delivery foreground` explicitly tests the empty
synthetic secure field. This component gate uses the population boolean,
which does not prove exact insertion; it refuses an already populated test field. A hidden-field
write may take effect while the official SDK reports unknown. That evidence is
retained without retry or upgrading completion.

## Real Provider functional campaign

`live_provider.py` exercises the ordinary AgentLoop with the production
OpenAI-compatible streaming adapter, DeepSeek `deepseek-flash`, and the real
pinned native SDK. Launch a fresh controlled Swift fixture with independent
state directory. Explicit desktop and Provider-network opt-ins are required:

```sh
PYTHONPATH=src /path/to/sdk-environment/bin/python evals/computer_use/live_provider.py \
  --allow-desktop --allow-real-provider \
  --fixture-app /path/to/MorrowComputerUseFixture.app \
  --fixture-state-file /path/to/fixture/state.json \
  --evidence-file /path/to/semantic-background.json \
  --mode semantic --delivery background
```

The key comes from `DEEPSEEK_API_KEY` or a hidden interactive prompt. Automated
callers can explicitly use `--credential-stdin` with an echo-disabled input
channel. Never put a key in command-line arguments or evidence files. Credentials
stay in the temporary composition's memory credential store.

Add `--matrix` to run semantic foreground/background cases, hybrid observation,
coordinate click/scroll and mouse gestures, plus 640-pixel resized and moved-window
coordinate checks. Every case starts a separate process and fixture instance;
this also avoids native workspace caches retaining previous process identities.

The default cases cover observation, token click, Unicode text, single key,
hotkey, scroll and synthetic secure input. Repeat with `--delivery foreground`.
Additional comma-separated `--cases` include `double_click`, `right_click`,
`postcondition_text`, `postcondition_exists`, `postcondition_attribute`, `denied`,
`coordinate_click` and `coordinate_scroll`. `--mode hybrid` explicitly requests
window screenshots and asks the model to stop on image refusal. A blocked image
case does not prove coordinate-action coverage. The `denied` case deliberately
rejects approval and must have zero SDK action entries.

Each case grants only the fixture's independent PID/window, creates a temporary
workspace/store, advertises only the two computer tools, limits model requests,
and admits at most one native action. The model chooses its own tool arguments;
responses are not scripted. Temporary journals and image artifacts are removed
on exit; exported JSON contains metadata, hashes, normalized outcomes and
independent state deltas. `raw_status=tested` means evidence was collected, **not**
that the feature passed. `status` is the strict verdict. Compare action status, requested variant, independent
effect and verification errors. Unknown outcomes remain unknown, with no action
retry or delivery fallback. Polling/cancellation/revocation, GUI interaction and
other applications need separate coverage.


The AppKit fixture records `liveText`/`liveSecureText`, committed `text`/population,
change counts and field identity separately. Key events record characters, target
field and key-up count; q/Y acceptance requires exact insertion and one event pair.
NSWindow update sampling observes edits that bypass NSControlTextDidChange, without
inventing change-notification counts. Use synthetic values only. Mouse events record
`mouseClickCounts` and `rightMouseEvents`; business button count is a separate oracle.
`live_provider.py --fixture-app /path/to/MorrowComputerUseFixture.app` launches and closes
a fresh instance for each case. Multiple cases require this flag. Verdicts are
`passed/failed/blocked/unsupported` and non-passing campaigns exit nonzero. Native unknown
is preserved even when an independent fixture effect or task condition passes.

Official SDK 0.30.4 cannot read an old token's exact native object attributes. The
functional SDK adds `read_element_attribute` for the retained object's Boolean
`enabled`, before refreshing the tree. Expired or mismatched objects stay
unavailable. Selector verification still requires a complete unique tree.

Fixture pointer events independently record window coordinates and wheel deltas. The scroll region
exports its AppKit window-space frame, so wheel acceptance proves the received
point was inside the real NSScrollView region as well as observing offset. Selected-node/frame metadata is
captured from the current trusted registry before dispatch, including pixel-routed gestures.

Official SDK 0.30.4 background double-clicks misdeliver AppKit window-local coordinates
on the tested host. The functional build supports left double-click on an observed
native element through one PID stream, with native click counts [1,2]. Official
builds and background coordinate/right double-click retain bounded refusals.

Foreground cases explicitly start their owned fixture as key/active and record those facts.
Official SDK 0.30.4 foreground pixel wheel delivery receives no fixture wheel events
on this host and remains refused. The functional build activates the selected
window and sends one exact-window PID wheel stream, retaining foreground delivery.
The 640-pixel moved-window scroll variant uses background delivery to exercise the working path.

The current fixture schema is v4. Non-finite pointer coordinates are explicit `null` with
`positionKnown=false`; they cannot block independent count/callback export and cannot prove
wheel-region delivery. New campaigns require healthy exports, valid effect fields and an advanced
snapshot revision for an action effect. Historical v1–v3 component records remain readable but
are not upgraded into new v4 acceptance evidence.

Pending picker selections share the candidate list's 30-second deadline. Claiming or consuming an
expired selection refuses it and requires selecting again. A valid selection is consumed once;
process birth and current window identity/geometry are still checked before observations and actions.
Action observation lifetime is independently configurable with
`morrow computer configure --max-observation-age-seconds 5 --expected-revision REV` (1–30 seconds,
default30). Tool replies include `expires_at`. Long values include an actual bounded `value_tail`
within the shared text budget, so a suffix may be read back without pretending the middle is visible.

The verdict permits fresh-observation recovery only for a same-call-ID proven
`stale_observation/not_started` attempt; native entry count must still be exactly one.
Unknown/completed actions never retry. The collector approves at most three such attempts and
records public reference/native token hash correspondence without `scripted_target`.
Postcondition scoring uses only the final action call ID's linked tool outcome,
with identical repeated history projections accepted. Earlier proven not-started
results remain recovery evidence. Missing or conflicting final outcomes fail
verification; historical passed/not_checked cannot replace the final predicate.

`run_live_controller.py` offers a receipt-only alternative to `live_provider.py`:

```bash
uv run python evals/computer_use/run_live_controller.py --allow-desktop \
  --fixture-app /path/to/MorrowComputerUseFixture.app \
  --fixture-state-file /path/to/state/state.json \
  --controller-directory /path/to/new-empty-bridge \
  --evidence-file /path/to/new-evidence.json \
  --case postcondition_text --seed-length 4097 --mode hybrid --delivery foreground
```

The runner launches only the explicitly supplied fixture and removes that process on exit.
The external controller reads `pending.json`, actual Morrow messages/tool schemas/images, then
atomically writes the requested decision path with `request_sha256`, `decision_basis`, and either
`tool={name, arguments}` or `content`. Use `live_bridge.atomic_json` to write receipts. It never
chooses tools/targets/coordinates, constructs no HTTP adapter, and reads no real credentials.
Per-turn `receipt-NNN-status.json` records received/accepted, both hashes, cancellation and bounded
error categories. Acceptance is a model decision, not SDK input. New requests never overwrite
old campaign files; use a fresh directory. Historical bridge copies remain unchanged.

## Five functional SDK gates

Build an independent fixture and install the default macOS arm64 extra:

```sh
uv run python evals/computer_use/build_fixture.py \
  --output-directory /tmp/morrow-five-fixture \
  --state-directory /tmp/morrow-five-state
UV_PROJECT_ENVIRONMENT=/tmp/morrow-five-env uv sync --locked --extra computer-use
PYTHONPATH=src /tmp/morrow-five-env/bin/python evals/computer_use/run_capabilities_fixture.py \
  --allow-desktop --fixture-app /tmp/morrow-five-fixture/MorrowComputerUseFixture.app \
  --fixture-state-file /tmp/morrow-five-state/state.json \
  --output-directory /tmp/morrow-five-evidence
```

This opt-in gate starts a fresh Python host and fixture instance for each case:
foreground/background exact enabled readback, foreground coordinate wheel,
background native element double-click and semantic AXScrollArea scroll. The
scripted controller selects published references and images through ordinary
AgentLoop; the independent fixture proves the actual native effects. It performs
one input per case and never retries unknown. The SDK completion must remain
unknown even when the independent effect passes. This is a reproducible native
regression, separate from Luna/API-model quality acceptance.

See [the functional build](../../vendor/cua-driver-functional/README.md) and
[acceptance evidence](../../docs/acceptance/computer-use-capabilities-2026-10-05.md).
