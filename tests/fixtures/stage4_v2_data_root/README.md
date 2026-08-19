# Stage 4 v2 Session fixture

Built from the Stage 3 YAML data root plus an Operational Store at schema v2
containing one empty `ses_v2fixture` Session. Tests construct the SQLite file
with `tests/fixtures/stage4_v2.py:write_v2_store` so the binary is not checked
in. Subplan 45 reuses this builder for upgrade acceptance.
