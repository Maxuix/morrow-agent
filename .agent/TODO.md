# TODO

Active review remediation: Stage 8 Subplan 2 (`2-future-graph-patch-continuation`).

- [x] Implementation, deterministic tests, full offline gate, documentation, local main integration
  and topic-branch retirement.
- [x] Verify review findings and repair the four confirmed contract bugs.
- [x] Preserve authoritative lineage budget/deadline reasons and expose effective Query outputs.
- [x] Run the full offline and static validation gates.
- [x] Commit and fast-forward the verified remediation into local `main`.
- [!] Push local `main` to `origin/main`; safety approval rejected the push pending explicit user
  authorization for this concrete GitHub publication.

Subplan 3 starts only after Subplan 2 publication closes, explicit activation, and authorization for
the additive `ApplicationEvent` lifecycle plus Python web-framework dependency.
