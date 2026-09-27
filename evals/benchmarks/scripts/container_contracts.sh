#!/usr/bin/env bash
# Model-free Linux/amd64 contract checks against the packaged Morrow wheel.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$BENCH_DIR/../.." && pwd)"
ASSETS="$BENCH_DIR/assets"
PY_TARBALL="$(find "$ASSETS" -maxdepth 1 -name 'cpython-3.12*-x86_64-unknown-linux-gnu-install_only.tar.gz' -print -quit)"
if [ -z "$PY_TARBALL" ]; then
  echo "missing CPython asset" >&2
  exit 2
fi
PY_TARBALL="$(basename "$PY_TARBALL")"

for image in "${MORROW_BENCH_COMPAT_IMAGE:-debian:11}" \
             "${MORROW_BENCH_TASK_IMAGE:-alexgshaw/fix-git:20251031}"; do
  echo "==> model-free contracts in $image ($(docker image inspect "$image" --format '{{.Id}}'))"
  docker run --rm --platform linux/amd64 --network none \
    -v "$ASSETS:/assets:ro" -v "$REPO_ROOT/tests:/tests:ro" \
    -w /tmp "$image" sh -ec '
      mkdir -p /opt/python
      tar -xzf "/assets/$1" -C /opt/python --strip-components=1
      /opt/python/bin/python3 -m venv /opt/morrow
      /opt/morrow/bin/pip install --no-index --find-links /assets/wheelhouse \
        /assets/morrow_agent-*.whl "pytest>=8.3,<9" "pytest-asyncio>=0.24,<1"
      /opt/morrow/bin/morrow --help >/dev/null
      /opt/morrow/bin/python -m pytest -c /dev/null -o asyncio_mode=auto \
        -q -m "not live" \
        /tests/test_shell_contract.py /tests/test_tracked_commands.py \
        /tests/test_run_deadline.py /tests/test_headless_run.py \
        /tests/test_harness_acceptance_fixes.py
    ' sh "$PY_TARBALL"
done

# An isolated fixture checks that a forced agent-container stop leaves a
# bind-mounted partial log readable by the host. It never targets a job container.
INTERRUPT_LOGS="$(mktemp -d)"
trap 'rm -rf "$INTERRUPT_LOGS"' EXIT
for signal in TERM KILL; do
  container="$(docker run -d --rm --platform linux/amd64 --network none \
    -v "$INTERRUPT_LOGS:/bench-logs" \
    "${MORROW_BENCH_TASK_IMAGE:-alexgshaw/fix-git:20251031}" \
    sh -c 'sleep 300')"
  docker exec "$container" sh -c \
    "printf '{\"availability\":\"partial\",\"unknown_request_count\":1}' > /bench-logs/$signal.json"
  docker kill --signal="$signal" "$container" >/dev/null
  test -s "$INTERRUPT_LOGS/$signal.json"
  docker rm -f "$container" >/dev/null 2>&1 || true
done
echo "==> external TERM/KILL mounted-log retention passed"
