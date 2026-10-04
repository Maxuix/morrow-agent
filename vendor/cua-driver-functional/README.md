# Cua Driver functional patch

`0.30.4+morrow.3` is the exact official `cua-driver-rs-v0.30.4` source plus
`functional.patch`. `manifest.json` binds the upstream commit and patch SHA256.
This patch provides retained-object `enabled` readback, real scroll-container
tokens, a foreground window wheel stream, and native background element double-click.
It contains no content classification, field filtering, redaction, or guarded SDK code.

The new read tool uses the authenticated runtime's token cache, retains the original
AX object, and checks its PID/window before and after reading. A disappeared object
stays unavailable. The tool belongs to the existing observation operation class.
Background doubles use real window-local coordinates and one public PID stream;
native container and foreground wheels use one PID/window stream. Foreground input
requires the exact selected window to become focused before dispatch. Completion
remains unverified unless the SDK has its own proof; Morrow never retries unknown.

The distributed wheel targets macOS arm64, with deployment target 14.0. Acceptance
was run on macOS 27.0.1; other platforms retain the official dependency and remain
unavailable in Morrow. See the October 5 acceptance report for the tested scope.

Reproduce with a checkout containing the pinned upstream commit and a macOS arm64
Rust toolchain. The preparation command archives source into a new empty directory;
it does not edit the checkout, fetch missing blobs, or execute native code.

```bash
git clone --filter=blob:none --sparse --branch cua-driver-rs-v0.30.4 https://github.com/trycua/cua.git /tmp/cua-upstream
git -C /tmp/cua-upstream sparse-checkout set libs/cua-driver/rust libs/cua-driver/python
uv run python evals/computer_use/prepare_functional_sdk.py --sdk-repository /tmp/cua-upstream --output-directory /tmp/cua-functional-source
uv run python evals/computer_use/build_functional_sdk.py --source-directory /tmp/cua-functional-source --output-directory /tmp/cua-functional-dist
```

The build uses upstream `Cargo.lock`, original UniFFI bindings, and an installed
Rust toolchain. `--offline` requires a prefilled Cargo cache. It verifies the prepared
files, builds the executable and SDK library, checks their code signatures, and emits
the wheel and `build-provenance.json`. Build output proves construction; the native
fixture campaign supplies independent effect evidence. No credentials are required.
