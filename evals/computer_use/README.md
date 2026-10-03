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
snapshot and masked capture, and checks the independent count. It never retries
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
can still have `image_share_error=image_safety_unconfirmed`. Neither mode opens
production gates.

The adapter bounds SDK traversal at 400 nodes, while model output remains capped
at 200 elements, depth 8 and 32 KiB of text. The pinned SDK counts collapsed
layout containers against traversal; those are not exported elements. Any
native truncation or projection omission still blocks image sharing. For bounded
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
  --expect-sdk present --sdk-version 0.30.4 --require-wheel --gui-source src/morrow/gui_static \
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
The verifier expects the official pinned SDK version 0.30.4.

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
synthetic secure field is populated. Secure-field bytes are never exported.
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
The SDK read/decode/masking/close runs on that full host's owner loop before its
local GUI serves. Output includes only bounded evidence and the actual loopback
URL. A held OS-assigned socket prevents conflicts and port-selection races.
Open that URL in an isolated test tab; normal scripted chat can verify that the
GUI composition still works after the SDK probe. Stop the process to close its
host/listener and remove its temporary state.

The host's ordinary computer-use lifecycle remains not_activated;
the probe does not create an active desktop run. This observes the fixed fixture via the opt-in
probe, not through a model grant. A partial/degraded image, missing two-field
mask proof, minimal service context or active production desktop fails the
probe; it never opens a diagnostic SDK endpoint to HTTP clients. This verifies
responsible-host/read coexistence, not the native action/Provider-image/product
end-to-end acceptance gates.

## Basic native input and ordinary loop

Use the official pinned SDK in a disposable environment. No custom package,
security-classification query or field-specific input guard is needed. Password
fields use the same authorized keyboard path as ordinary fields; existing AX
values and screenshots still receive content protection.

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
synthetic secure field. Its independent oracle reads only the population boolean,
never secure bytes; it refuses an already populated test field. A hidden-field
write may take effect while the official SDK reports unknown. That evidence is
retained without retry or upgrading completion.

## Real Provider functional campaign

`live_provider.py` exercises the ordinary AgentLoop with the production
OpenAI-compatible streaming adapter, DeepSeek `deepseek-flash`, and the real
pinned native SDK. Launch a fresh controlled Swift fixture with independent
state first. Explicit desktop and Provider-network opt-ins are required:

```sh
PYTHONPATH=src /path/to/sdk-environment/bin/python evals/computer_use/live_provider.py \
  --allow-desktop --allow-real-provider \
  --fixture-state-file /path/to/fixture/state.json \
  --evidence-file /path/to/semantic-background.json \
  --mode semantic --delivery background
```

The key comes from `DEEPSEEK_API_KEY` or a hidden interactive prompt. Automated
callers can explicitly use `--credential-stdin` with an echo-disabled input
channel. Never put a key in command-line arguments or evidence files. Credentials
stay in the temporary composition's memory credential store.

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
independent state deltas. `status=tested` means evidence was collected, **not**
that the feature passed. Compare action status, requested variant, independent
effect and verification errors. Unknown outcomes remain unknown, with no action
retry or delivery fallback. Polling/cancellation/revocation, GUI interaction and
other applications need separate coverage.
