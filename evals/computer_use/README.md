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
  --expect-sdk present --require-wheel --gui-source src/morrow/gui_static \
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

The host's ordinary computer-use lifecycle remains not_activated and
native_verified stays false. This observes the fixed fixture via the opt-in
probe, not through a model grant. A partial/degraded image, missing two-field
mask proof, minimal service context or active production desktop fails the
probe; it never opens a diagnostic SDK endpoint to HTTP clients. This verifies
responsible-host/read coexistence, not the native action/Provider-image/product
end-to-end acceptance gates.

## Exact-token SDK security prototype

`native_security_readonly.py` explicitly loads a separately built experimental
SDK package whose dylib hash must match the supplied value. It never replaces
installed SDK files or enables production native input:

```sh
/tmp/morrow-computer-use-sdk-check/bin/python evals/computer_use/native_security_readonly.py \
  --allow-desktop --fixture-bundle-id com.morrow.ComputerUseFixture \
  --fixture-state-file /tmp/morrow-computer-use-scroll-evidence/state.json \
  --prototype-package-directory /tmp/morrow-cua-sdk-security-prototype/python \
  --prototype-dylib-sha256 VERIFIED_BUILT_SHA256 \
  --evidence-file /tmp/morrow-security-query.json
```

The SDK prototype resolves the current token inside its own retained AX cache,
checks PID/window ownership and bounded ancestry, and returns only a closed
security classification. The harness validates that response and checks the
independent fixture file is unchanged. A sensitive field plus an unknown normal
field fails the gate; binding proof alone never establishes non-sensitive.
Native admission-time input protection, async Morrow integration, reproducible
patched wheels and complete native acceptance remain separate work. Local
prototype source patch, license, source/binary hashes and actual evidence live
in the ignored acceptance assets; it is not a released SDK or supported package.
