# Progress Tracker

## Current status

S7P-00 implementation and offline validation are complete on `codex/feat/s7p-00-eval-protocol` from verified
`main@f69a0ce9ec7efc6a7eea07f7c5a1bb36945e9dfa`. The full user-provided reliability checklist has
been read. The evaluation-only harness now has a versioned protocol, strict non-secret profile,
create-only Run Manifest, rebuild/finalize lifecycle, integrity-linked evidence and mechanical
aggregation; production runtime files remain unchanged.

The current worktree also contains three user-owned untracked research documents, including the
reliability checklist. They are not part of the topic branch and must remain untouched.

## Active task

Add failing protocol/rebuild/finalization/aggregation tests, then implement the evaluation-only
run-bundle lane without changing production runtime code.

## Located evidence

- `eval.py --help` exposes compatibility commands plus `start/rebuild/finalize/summarize`.
- `run-manifest.json` freezes task/repetition/source/dataset/protocol/workspace/profile snapshots;
  the Agent workspace marker remains free of the expected-change policy.
- Finalized bundles retain verifier output, bounded Git status/diff, runtime/stop evidence and
  artifact hashes; summary rejects tampering, duplicates, mixed revisions and unavailable metrics.
- `results-template.csv` is retired and the README documents two fresh repetitions and the bundle
  workflow.
- No committed raw result JSON/JSONL exists from which the legacy acceptance totals can be rebuilt.

## Next action

Initial implementation commit is ready; stop for the independent review subagent. Keep the three
user-owned untracked research documents and the historical Stage 7 baseline untouched; do not
merge `main` or delete the topic branch/worktree.

## Blockers

None.
