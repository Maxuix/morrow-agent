#!/usr/bin/env bash
# Prepare the offline asset bundle used to install Morrow inside task containers.
#
# Assets (all linux/x86_64 — Terminal-Bench 2.0 and SWE-bench images are amd64):
#   assets/morrow_agent-*.whl                 Morrow wheel built from this repo
#   assets/cpython-3.12.*-install_only.tar.gz python-build-standalone (USTC mirror)
#   assets/wheelhouse/                        all Morrow runtime deps (x86_64 wheels)
#   assets/uv-x86_64-unknown-linux-gnu        optional static uv binary
#
# Network notes (CN): PyPI via tencent mirror, docker hub via daocloud mirror,
# python-build-standalone via USTC github-release mirror.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$BENCH_DIR/../.." && pwd)"
ASSETS="$BENCH_DIR/assets"
PY_BUILD_TAG="20260901"
PY_VERSION="3.12.14"
PY_TARBALL="cpython-$PY_VERSION+$PY_BUILD_TAG-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYPI_MIRROR="${UV_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple/}"
COMPAT_IMAGE="${MORROW_BENCH_COMPAT_IMAGE:-debian:11}"

mkdir -p "$ASSETS"
STAGING="$(mktemp -d "$ASSETS/.build.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/wheelhouse"

echo "==> building current Morrow wheel"
(cd "$REPO_ROOT" && uv build --wheel --out-dir "$STAGING")

echo "==> preparing CPython $PY_VERSION"
if [ ! -f "$ASSETS/$PY_TARBALL" ]; then
  curl -fL --retry 3 -o "$STAGING/$PY_TARBALL" \
    "https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/$PY_BUILD_TAG/cpython-$PY_VERSION%2B$PY_BUILD_TAG-x86_64-unknown-linux-gnu-install_only.tar.gz"
  mv "$STAGING/$PY_TARBALL" "$ASSETS/$PY_TARBALL"
fi

echo "==> resolving wheels for CPython 3.12, manylinux_2_28 x86_64"
PLATFORMS=()
for minor in $(seq 28 -1 5); do
  PLATFORMS+=(--platform "manylinux_2_${minor}_x86_64")
done
PLATFORMS+=(--platform manylinux2014_x86_64 --platform manylinux2010_x86_64)
PLATFORMS+=(--platform manylinux1_x86_64)
docker run --rm --platform linux/amd64 \
  -v "$ASSETS:/assets:ro" -v "$STAGING:/candidate" "$COMPAT_IMAGE" \
  sh -ec 'tarball=$1; index_url=$2; shift 2; \
    mkdir -p /opt/python; \
    tar -xzf "/assets/$tarball" -C /opt/python --strip-components=1; \
    /opt/python/bin/python3 -m venv /opt/pipenv; \
    /opt/pipenv/bin/pip download --disable-pip-version-check --no-cache-dir \
    --index-url "$index_url" --only-binary=:all: \
    "$@" --python-version 3.12 \
    --implementation cp --abi cp312 \
    --dest /candidate/wheelhouse /candidate/morrow_agent-*.whl' \
    sh "$PY_TARBALL" "$PYPI_MIRROR" "${PLATFORMS[@]}"

echo "==> testing offline install in $COMPAT_IMAGE"
docker run --rm --platform linux/amd64 --network none \
  -v "$ASSETS:/assets:ro" -v "$STAGING:/candidate:ro" "$COMPAT_IMAGE" \
  sh -ec 'mkdir -p /opt/python; \
    tar -xzf "/assets/$1" -C /opt/python --strip-components=1; \
    /opt/python/bin/python3 -m venv /opt/morrow; \
    /opt/morrow/bin/pip install --no-index --find-links /candidate/wheelhouse \
      /candidate/morrow_agent-*.whl; \
    /opt/morrow/bin/morrow --help >/dev/null' sh "$PY_TARBALL"

rm -f "$ASSETS"/morrow_agent-*.whl
mv "$STAGING"/morrow_agent-*.whl "$ASSETS/"
rm -rf "$ASSETS/wheelhouse"
mv "$STAGING/wheelhouse" "$ASSETS/wheelhouse"
echo "==> compatible asset bundle ready"
