# Controlled native computer-use gates

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

The script accepts only this fixture identity and requires exactly one running
window. It creates a named Session, discovers the fixture, reads its AX tree and
screenshot, checks encoded dimensions by decoding, then ends the Session and
shuts down the Driver. It prints bounded metadata, counts, dimensions and hash;
SDK objects, AX text, image bytes, input values and native exceptions are absent
from its output. A capture stays transient and is never sent to a Provider.

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
