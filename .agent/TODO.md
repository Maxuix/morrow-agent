# TODO

## Current stage

Subplan 90 is active on `feat/s7p-09-direct-pi-baseline-run`, intentionally stacked from verified
local Stage 7 work. Subplan 91 is completed and ready to fast-forward into this execution branch.

## Active subplan

S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline.

## Tasks

- `[x]` Build and offline-verify the strict 28-run comparison harness, safe normalizers,
  permission-equivalence proof, immutable bundles and capacity accounting.
- `[x]` Repair Provider streaming, Pi 0.84.2 events, terminal observation, slash-command target
  inference and durable usage fallback exposed by retained campaigns r2–r6.
- `[x]` Complete Subplan 91: replace the legacy core coding-tool schema with the verified
  Pi-aligned seven-tool compatibility surface while retaining execution-side safety/recovery.
- `[x]` Commit and fast-forward Subplan 91 into the S7P-09 execution branch; audit the new 9-tool
  default and seven-tool core profile sizes.
- `[!]` Refreeze clean source/profile/schedule/evidence pins: blocked because the approved 50M total
  cannot cover 16,754,419 known retained tokens + 42M fresh reservation + six unknown requests.
- `[ ]` Execute and finalize the exact 20 Morrow primary runs if admission passes.
- `[ ]` Execute and finalize the exact 8 counterbalanced Pi comparison runs if admission passes.
- `[ ]` Mechanically aggregate, compare, classify every failure and freeze the Direct baseline.
- `[ ]` Publish S7P-09 acceptance evidence and raw-bundle hash index without sensitive payloads.
- `[ ]` Run final offline/static/CLI gates, integrate verified work into `main`, then stop before
  S7P-10.

## Boundaries

- Do not change protocol v1 thresholds, tasks, Gold, expected paths or verifiers.
- Do not reuse r2–r6, mix profiles/models/sampling, or replace an admitted result.
- Do not exceed the approved 50M total token ceiling; cost remains optional/unlimited.
- Do not commit credentials, reasoning, transcripts, raw Pi JSONL or full tool payloads.
- No Workflow, multi-Agent or S7P-10 work starts here.
