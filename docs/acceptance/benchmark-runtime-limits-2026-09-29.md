# Benchmark runtime limits repair (2026-09-29)

The paused `tb2-v2-full-20260928T220623Z` job is historical evidence. Its 23 official
rewards must not be combined with a job built from this source. This change does not
restart Harbor or the paused monitor.

## Decision

- A response exceeding 256 KiB now settles its admitted model request as failed with
  `invalid_response`, then records the run's `model_output_limit` terminal metrics.
  Usage for a stream stopped before provider completion remains unavailable, not zero.
- The 256 KiB streaming guard stays. A durable conversation record has the same 256 KiB
  payload budget; removing only the streaming guard would move the failure to persistence.
  The `dna-assembly` stream had already produced at least 61,440 chunks in 728 seconds,
  so an unbounded reply also risks more time and token exposure without evidence of a
  usable completion. Supporting larger replies requires a separate storage and delivery
  design, with bounded memory and tests for recovery and history replay.
- New Terminal-Bench v2 runs use a 2.0 Harbor agent timeout multiplier by default.
  This changes a 1,200-second task to 2,400 seconds and a 1,800-second task to 3,600
  seconds. The longest 12,000-second task can now run for 24,000 seconds. The multiplier
  is explicit in each new run fingerprint and can be overridden with
  `--agent-timeout-multiplier`; old runs retain their frozen 1.0 setting. Morrow still
  reserves time for persistence and log export before the Harbor deadline.

## Verification

- `uv run pytest -q tests/test_agent_run_observability.py`: 40 passed.
- `uv run pytest -q tests/test_workflow_repeat_interruption.py tests/test_run_deadline.py`:
  8 passed.
- `uv run pytest -m 'not live'`: 2,576 passed, 2 deselected.
- `PYTHONPATH=../../src .venv/bin/python -m pytest -q tests/test_run_tb2.py tests/test_morrow_harbor_agent.py`
  from `evals/benchmarks`: 31 passed.
- `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run python -m compileall -q src tests`, and `git diff --check`: passed.

The changed timeout policy is a new benchmark configuration. No official score from
this policy exists yet; its future score must be labelled with the 2.0 multiplier
when compared with the original 1.0 campaign. The missing usage or cost of the
historical fifth `dna-assembly` request cannot be recovered from these tests.
