# Subplan 58 — Semantic Preference Reviewer and Inbox

> Status: completed
> Branch: `feat/stage5-preference-reviewer`
> Prerequisite: Subplan 57 merged
> Owns: minimal Reviewer protocol, bounded context, proposal validation and decisions

## Goal

Create the Preference-only semantic path from a complete user message to independent Inbox
proposals. This subplan supports explicit/manual Review execution for deterministic testing; S59
adds automatic enqueue and background scheduling.

## Tasks

### S58.1 Frozen Review context

- Add a context builder that receives one current user record, its one `pev_` Evidence ID, a small
  bounded recent dialogue window, and the v13 frozen Active Preference snapshot (all Active durable
  entries, at most 256/192 KiB), not current YAML.
- Exclude system prompts, private reasoning, tool payloads/results, credentials, raw Artifacts, and
  unbounded conversation history. Assistant text is clearly reference-only.
- Preserve the complete current user message within the existing durable message limit; if the
  configured Reviewer request budget cannot hold the complete message plus snapshot, end with a
  terminal non-retryable budget failure instead of keyword-truncating it.
- Persist context digest, source document revisions, and bounded active snapshot with the job so
  delayed review is reproducible.

### S58.2 Minimal no-tool Reviewer

- Add `ModelPreferenceReviewer` with the one `operations` schema from the master plan in the first
  request. Do not expose the broad Learning Candidate union.
- Run one no-tool Provider completion per attempt; there is no repair completion. Strictly parse the
  root object and add/replace/remove shape, enforce request/response budgets, and sanitize all
  Provider errors.
- Accept learned scope only from `{global, workspace}` and require `evidence_ids` length exactly one,
  equal to the job's current-user `pev_` ID. Reject session scope rather than coercing it.
- Allow zero operations as success. Never persist raw response or Provider reasoning.
- Provide a deterministic fake/scripted Reviewer for offline integration tests; do not claim it
  measures semantic quality.

### S58.3 Deterministic proposal pipeline

- Verify each Evidence ID belongs to the same job/workspace and the current user source, and that no
  cited Evidence was safety-rejected.
- Enforce operation count/shape, statement safety/length, scope, frozen target existence/revision,
  unique targets, and exact duplicate rules: active/disabled same-scope matches block add; deleted
  tombstones do not. Do not add semantic marker/category classifiers.
- Persist one immutable proposal per valid operation with stable fingerprint and expected target/
  document revisions. Duplicate Review replay attaches no duplicate proposal.
- Keep policy/suppression checks deterministic and proposal-scoped; zero valid proposals still
  completes the job successfully.

### S58.4 Inbox and decisions

- Implement typed Preference proposal list/show/preview in `application/preferences/inbox.py` and
  dedicated UI adapters. The existing 900+ line Learning Inbox receives only thin type dispatch.
- Implement accept, edit-and-accept, reject, reject-and-suppress, and same-scope accept-many through
  the S57 Writer. Preview and final OCC tokens must be identical.
- A stale target/document revision is visible and cannot silently retarget or overwrite. Edited
  operations retain original proposal and Evidence.
- Reuse the S57 compatibility translator for unresolved historical fixed-field Preference Candidates;
  no new Reviewer output or public DTO may create the old payload.

### S58.5 Legacy broad Reviewer boundary

- Prevent the existing broad accepted-Task Reviewer from creating new fixed-field Preference
  Candidates after Preference v2 is enabled.
- Preserve its non-Preference Profile/Project Knowledge/future-candidate behavior and historical
  reads. Do not fold those domains into the Preference Reviewer.
- Remove Preference-specific keyword authority as a gate for the new path; retain only source,
  actor, Evidence integrity, and safety facts.

## Primary files

- `src/morrow/core/preference_review.py`
- `src/morrow/adapters/models/preference_reviewer.py`
- `src/morrow/application/preferences/context.py`
- `src/morrow/application/preferences/proposals.py`
- `src/morrow/application/preferences/inbox.py`
- focused composition/API and learning compatibility edits

## Acceptance

- Natural paraphrases without “以后/默认/always/remember” can become scripted add, replace, and
  remove proposals.
- Temporary, quoted, hypothetical, Assistant-only, invented-Evidence, secret, hidden-control,
  injection, and capability cases yield zero Active writes.
- The first request contains the complete minimal schema; no repair call or broad Candidate union is
  sent.
- A multi-operation response creates independent proposals; same-scope accept-many writes one YAML
  revision and finalizes all decisions together.
- Existing Project Knowledge/Profile flows and historical Preference Candidate rendering remain
  functional.

## Validation

```bash
uv run pytest -q tests/test_preference_reviewer.py tests/test_preference_review_context.py \
  tests/test_preference_proposals.py tests/test_preference_inbox.py \
  tests/test_stage5_review_pipeline.py tests/test_stage5_learning_cli.py \
  tests/test_stage5_project_knowledge.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow learning --help
git diff --check
```

## Review and closeout

After the implementation checkpoint and gates pass, invoke exactly one `$grok-delegate` `/review`
for S58 and wait for completion. Independently adjudicate every issue/suggestion, fix the confirmed
and valuable subset once, rerun affected/S58 gates, and do not request a second review. Commit,
fast-forward merge, retire the clean branch, and activate S59.
