# Stage 3 data-root fixture

Frozen Stage 3 YAML-only data root used to prove the Subplan 36 v1 Operational
Store upgrade does not rewrite Profile, Preferences, Provider config, or the
workspace index.

There is no `store/` directory. Subplan 45 reuses this fixture for the packaged
Stage 3-to-v1 acceptance path.
