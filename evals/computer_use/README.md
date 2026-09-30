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
