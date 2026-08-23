# Stage 5 Preference v2 Post-Implementation Checklist

> Status: simulated-user and reviewed real-Provider protocols passed on 2026-08-23

Run only after S56–S61 are merged, the separate integrated Grok review/fix is complete, the full
non-live gate is green, and the working tree contains no implementation changes. Preserve the old
`0/3` add/overwrite, `0/2` remove, timeout, and restored-Session stale evidence as the baseline.

## Offline and simulated-user protocol

- Verify `/preferences` and `preferences inbox` list/show/accept/edit/reject/retry paths in a fresh
  isolated state root.
- Exercise direct add/replace/remove/disable/enable and inferred Inbox operations, then restart the
  process and confirm YAML revision, entry ID, lifecycle, Evidence, proposal, and write-batch links.
- Change Preference YAML between two Turns of one restored Session. Confirm the first AgentRun stays
  frozen and the next AgentRun sees the new rule; verify Project Knowledge MemorySelection remains
  independently observable.
- Simulate Reviewer timeout, malformed output, lease expiry, third-attempt exhaustion, stale target,
  YAML OCC conflict, and crash after YAML apply. Confirm foreground Turn results remain unchanged and
  recovery never overwrites unrelated YAML drift.
- Run doctor before/after and create/verify an SQLite backup. Restore YAML separately from its state
  backup and SQLite/Artifacts from the bundle; confirm no credential material is copied.

## Opt-in real-Provider protocol

- Use only the versioned `preference-v2-natural-language-v1` corpus and an isolated state/report root.
- Report all frozen numerators and denominators: positive operations, proposal precision,
  replace/remove targets, safety-negative Active writes, and next-AgentRun adherence.
- Record bounded provider/model/prompt/schema IDs, attempts, and latency only. Never persist raw
  Provider output, reasoning, credentials, or unredacted safety-negative text.
- Pass requires at least `11/12`, `90%`, `6/7`, `0`, and `9/10` respectively. Any missing denominator
  or unexecuted probe is pending, not a pass.

## Completion rule

Update Stage 5 from “implementation complete; acceptance pending” only after both protocols have
their evidence committed and independently reviewed. Do not begin Stage 6 merely because S61 code
and offline tests pass.

## 2026-08-23 execution evidence

The simulated-user protocol ran from local `main` baseline `4e48b1c` on the dedicated
`test/stage5-preference-v2-acceptance` branch. Every selected test uses isolated pytest state roots;
no real Provider, credential, network, user YAML, or user Operational Store was used.

- Command scope: Preference CLI/Inbox, direct Writer lifecycle, Review context/jobs/Reviewer,
  worker lease/retry, migration/restore, restored-Session context refresh, configuration promotion,
  MemorySelection separation, crash recovery, doctor, and backup verification.
- Result: `158 passed in 5.22s`.
- The set covers add/replace/remove/disable/enable, accept/edit/reject/retry, restart/OCC/recovery,
  timeout/malformed/lease/exhaustion paths, stale targets, crash after YAML apply, doctor, isolated
  SQLite backup, and separate YAML authority.
- The live gate checked only whether `MORROW_OPENCODE_GO_API_KEY` was non-empty and returned
  `absent`. Its value was never read or printed. `pytest -m live` was therefore not run.

This evidence completed the simulated-user half. The later reviewed Reviewer v4 live replay passed
with positive operations `12/12`, precision `14/14`, targets `8/8`, safety-negative Active writes
`0`, and next-AgentRun adherence `10/10`; see
[`stage5-preference-v2-live-report-v4-final-2026-08-23.json`](stage5-preference-v2-live-report-v4-final-2026-08-23.json).
