# Stage 5 Preference v2 compatibility fixtures

These fixtures are read-only inputs for the S56 migration and compatibility
tests. They are not active user state and are never published by the tests.

| Fixture | Expected handling |
|---|---|
| `legacy_global.yaml` | Decode v1 fixed fields in memory; preserve Providers and `active_model`; publish only through an explicit migration plan. |
| `legacy_workspace.yaml` | Decode workspace v2 fixed fields into independent workspace entries. |
| `cleared_workspace.yaml` | Preserve the cleared envelope and produce no active entries. |
| `legacy_agent_run.json` | Decode the immutable old snapshot on read; never rewrite it. |
| `legacy_candidate.json` | Keep the old Candidate payload readable for the S57 compatibility translator. |
| `future_global.yaml` | Refuse with a bounded future-schema reason. |
| `corrupt_workspace.yaml` | Refuse with a bounded corrupt-state reason. |
