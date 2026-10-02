# Cua Driver guarded-input patch

This is Morrow's experimental macOS security extension to the pinned Cua Driver
0.30.4 source. It preserves upstream MIT licensing and does not constitute an
upstream release or cross-platform support claim. The main optional dependency
is unchanged and production native support remains disabled.

The patch resolves the current opaque token in the SDK retained AX cache,
checks exact PID/window ancestry, and provides the closed schema-2 security
query. Guarded text, key and hotkey requests recheck the same retained object
before native input; keyboard delivery also requires exact native focus.
Unknown controls, unreadable facts, unsupported ancestors and secure fields
cannot grant input. It is not general screenshot DLP.

`manifest.json` binds the exact upstream Git commit and patch SHA256. These
source materials are tracked so they can be reproduced without an agent's
temporary checkout. A release wheel, install matrix and production dependency
integration remain to be completed. Native component evidence is separate from
release-package acceptance.

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
