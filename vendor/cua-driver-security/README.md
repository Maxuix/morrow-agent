# Cua Driver guarded-input patch

This is Morrow's experimental macOS security extension to the pinned Cua Driver
0.30.4 source. It preserves upstream MIT licensing and does not constitute an
upstream release or cross-platform support claim. The macOS arm64 optional
dependency now pins this guarded candidate by URL and SHA256; production native
support remains disabled until the full product gates pass.

The patch resolves the current opaque token in the SDK retained AX cache,
checks exact PID/window ancestry, and provides the closed schema-2 security
query. Guarded text, key and hotkey requests recheck the same retained object
before native input; keyboard delivery also requires exact native focus.
Unknown controls, unreadable facts, unsupported ancestors and secure fields
cannot grant input. It is not general screenshot DLP.

Guarded native hotkeys additionally use the existing key readback policy on
the same retained input object. Confirmation requires readable changed native
value/selection and a successful security recheck after input. Failed or
unchanged readback remains unverifiable. Legacy hotkeys and arbitrary controls
do not gain this confirmation path; field values never enter the result.

`manifest.json` binds the exact upstream Git commit and patch SHA256. These
source materials are tracked so they can be reproduced without an agent's
temporary checkout. The local `0.30.4+morrow.1` macOS arm64 release candidate
has passed installation, ABI import, ordinary-task isolation and exact native
security/refusal checks on Python 3.12 and 3.13. One guarded Unicode insertion
also passed using the installed release wheel. The production owner now composes
the exact security query and mandatory native input guard. Complete native product
acceptance remains pending.

The [candidate release](https://github.com/Maxuix/morrow-agent/releases/tag/cua-driver-morrow-v0.30.4.1)
provides the wheel, exact patched source, source/patch checksums, MIT license,
third-party notices and registry source archives for the SDK/CLI build graph.
The wheel SHA256 is `3723f8e55a4933447015c0efa1a967fb6b8f83e502751376741df18433062179`.

Prepare an empty source directory from a local upstream clone:

```sh
uv run python evals/computer_use/prepare_sdk_source.py \
  --sdk-repository /path/to/cua-clone \
  --output-directory /tmp/morrow-cua-sdk-source-build
```

The script verifies the manifest hash, archives the exact SDK Rust/Python
subtrees, checks and applies the patch, and records source provenance. Git lazy
fetch and credential prompting are disabled: missing source objects fail rather
than triggering network access. It never edits the existing checkout or starts
a native Driver. Build dependencies must be provisioned separately.

Build a local wheel on macOS arm64 with Rust 1.97.1, uv and cached build
dependencies available:

```sh
uv run python evals/computer_use/build_sdk_wheel.py \
  --sdk-repository /path/to/cua-clone \
  --output-directory /tmp/morrow-cua-wheel-build \
  --cargo-target-directory /tmp/morrow-cua-cargo-target
```

The output directory must be empty and separate from Cargo's target directory.
The builder prepares fresh verified source, builds the SDK and bundled CLI
with `--release --locked --offline`, and uses an explicit macOS 14 arm64 tag.
It disables release stripping: stripped proc-macro dylibs failed to load on
the acceptance host with a mis-aligned LINKEDIT string pool. The wheel includes
the MIT license and source/binary provenance. Its distribution and Python module
versions both identify Morrow's local variant; the native upstream ABI version
is retained. No upstream release is downloaded, Driver started, wheel installed
or artifact published by this builder.

Build evidence marks installed validation false until separate checks run.
Hashes identify the actual artifact, without promising identical native binary
bytes across toolchains or build paths. Production diagnostics recognize this
exact local version while continuing to report native support unavailable.
