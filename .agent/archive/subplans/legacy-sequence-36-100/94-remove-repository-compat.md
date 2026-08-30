# Subplan 94 — Remove Repository Compatibility Surfaces

Status: completed, verified and fast-forward integrated into local `main` at `db7835c`.

## Objective

Delete Morrow-version compatibility that has no current production responsibility and migrate
internal imports/tests to canonical contracts. Leave persisted YAML/SQLite/Backup transformations
for the next verified slice.

## Ownership

- compatibility re-export modules and package exports
- unused compatibility aliases, properties and method spellings
- project-instruction unreachable refresh compatibility
- current module import ownership in `src/` and `tests/`
- historical evaluation-only loaders, resources, fixtures and documentation redirects

## Acceptance

- No deleted symbol has a production consumer.
- Tests use current contracts rather than preserving compatibility APIs.
- Historical evaluation-only data is removed without touching retained Live evidence.
- Focused and complete offline/static/CLI/diff gates pass.

## Validation

- Focused repository/API/prompt/policy/Skill/MCP suites passed.
- Complete offline gate: `1338 passed, 2 deselected in 112.98s`.
- Ruff format/check, compileall, both CLI help checks and `git diff --check` passed.
- No Live or network test was run.
