# TODO

## Current stage

Stage 3 is closed on the claimed macOS platform. Stage 4 is open for planning, but no Stage 4
implementation subplan is active.

## Active subplan

None.

## Tasks

- [x] S3.34.1 Implement safe Git inspection.
- [x] S3.34.2 Lock the final production tool inventory.
- [x] S3.34.3 Run complete Fake Provider product stories.
- [x] S3.34.4 Run real terminal and security acceptance.
- [x] S3.34.5 Create requirement-to-evidence matrix.
- [x] S3.34.6 Reconcile product and architecture documentation.
- [x] S3.34.7 Run final quality and package gates.

Subplan 34 completed on 2026-08-18 after read-only Git hardening, two Fake Provider product
stories, real terminal/security acceptance, host-level macOS Seatbelt tests, full offline and
package gates, and the requirement-to-evidence matrix. The external review's nine bugs, two
suggestions, and one nit were remediated. On 2026-08-19 the persistent Mimo v2.5 environment,
Keychain error boundary, connection feedback, preset discovery, wrapper state routing, and final
quality/package gates were rechecked. Current macOS Auto Sandboxed is native and fail-closed;
Linux runtime remains explicitly unsupported until a real runner passes. Full Access and Stage 4
persistence remain unimplemented and are now the next planning scope.
