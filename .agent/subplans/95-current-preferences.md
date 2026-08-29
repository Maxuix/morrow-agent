# Subplan 95 — Generic Preferences as the Only Current Format

Status: verified; ready to integrate from `refactor/current-preferences` into `main`.

## Objective

Remove fixed-field Preference models, dual Session projections, decode-only snapshot compatibility
and v1/v2 YAML runtime readers. Keep the generic global/workspace documents as the sole runtime
authority. Provide a deterministic one-shot migration for supported fixed-field YAML before current
documents are loaded, then remove the old decoder and historical fixtures.

## Ownership

- Preference domain documents, YAML stores and migration boundary
- Session/AgentRun Preference snapshot and projection fields
- bootstrap, ContextBuilder, Turn lifecycle and Review preparation consumers
- fixed Preference configuration commands and services
- focused Preference/Session/AgentRun tests and current architecture documentation

## Acceptance

- Runtime models contain no fixed `Preferences` compatibility projection or `legacy_preferences`.
- Current YAML and AgentRun snapshots have one accepted schema shape.
- Supported user YAML migration completes before current loading; unsupported history fails clearly.
- Historical Preference fixtures and decode-only tests are deleted.
- The fixed-field Learning Preference Candidate path and old backup format are absent.
- Focused and complete offline/static/CLI/diff gates pass.
