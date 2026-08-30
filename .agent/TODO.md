# TODO

## Current task

None. Subplan 99 is complete and verified.

## Tasks

- `[x]` Add the shared typed failure contract and migrate Adapter output.
- `[x]` Reduce ModelCallRunner and AgentLoop to one failure/retry path.
- `[x]` Update fixtures, consumers and focused tests.
- `[x]` Run complete offline/static validation.
- `[x]` Commit, integrate and retire Subplan 99.

## Boundaries

- No new Provider probes, admission gates, content validators or dependencies.
- No live Provider/network run.
- No raw exception, SDK object, credential or traceback in events or durable state.
