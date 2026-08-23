# TODO

## Current stage

Stage 5 Preference v2 post-implementation acceptance.

## Active subplan

None. Real-Provider acceptance is on hold pending the explicit harness credential.

## Tasks

- `[x]` S61.1 Retire obsolete active fixed-field and marker-classifier Preference paths.
- `[x]` S61.2 Complete v13/YAML doctor, backup, and isolated restore acceptance.
- `[x]` S61.3 Add the versioned offline corpus and opt-in real-Provider scoring harness.
- `[x]` S61.4 Reconcile architecture, roadmap, README, acceptance, help, and migration docs.
- `[x]` S61.5 Run focused/full gates and create the implementation checkpoint.
- `[x]` Run exactly one S61 Grok `/review`, independently fix confirmed findings, and rerun gates.
- `[x]` Commit S61 closeout, fast-forward `main`, and retire the S61 branch.
- `[x]` Run exactly one integrated S56–S61 Grok `/review` against the merged implementation.
- `[x]` Independently fix confirmed integrated findings once; do not re-review the fixes.
- `[x]` Run the complete non-live and repository quality gates.
- `[x]` Commit and fast-forward the integrated closeout to local `main`, retire the branch, and
  leave simulated-user/real-Provider acceptance pending.
- `[x]` Run the isolated simulated-user protocol and record bounded evidence.
- `[x]` Verify whether the explicit live credential gate is satisfied without reading the secret.
- `[!]` If satisfied, run the versioned live corpus in an isolated report root; otherwise record
  live acceptance as pending.
- `[x]` Review acceptance evidence and run final non-live gates.
- `[x]` Commit the bounded evidence, fast-forward it to local `main`, and close the branch.

## Start condition

- Start from verified local `main` at `1a8e38b` after S60 merged and its final offline gates passed;
  v13 Review persistence, Writer/Reviewer/Inbox, and next-AgentRun injection contracts are frozen.
- Preserve the untracked user files `docs/research/stage5-overview-pipeline.md` and
  `docs/research/stage5-overview-review.md`; do not stage or modify them without explicit request.
- Do not run a real Provider or access credentials during implementation subplans. Use injected
  scripted Providers/Reviewers, clocks, futures, and schedulers.

## Required execution discipline

Use one logical S61 task at a time on its dedicated branch. Preserve the frozen v13, Writer,
Reviewer, worker, and AgentRun boundaries while retiring only proven-dead compatibility code. Keep
doctor/backup output bounded and read-only; do not run the opt-in real-Provider corpus. Commit one
verified implementation checkpoint, perform exactly one `$grok-delegate` `/review`, independently
fix confirmed/valuable findings without a second review, run final gates, commit closeout,
fast-forward merge, and retire the branch before the separate integrated review.
