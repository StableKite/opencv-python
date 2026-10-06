#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <image> <expected-platform> <wheel-output-dir> <build-sdist:0|1>" >&2
  exit 2
fi

IMAGE="$1"
EXPECTED_PLATFORM="$2"
WHEEL_OUTPUT_DIR="$3"
BUILD_SDIST="$4"

case "$BUILD_SDIST" in
  0|1) ;;
  *) echo "BUILD_SDIST must be 0 or 1" >&2; exit 2 ;;
esac

rm -rf "$WHEEL_OUTPUT_DIR"
mkdir -p "$WHEEL_OUTPUT_DIR"
if [ "$BUILD_SDIST" = "1" ]; then
  rm -rf ci-dist-sdist
  mkdir -p ci-dist-sdist
fi

docker run --rm \
  --user 0 \
  -e PYTHON_JIT=1 \
  -e PYTHON_GIL=0 \
  -e EXPECTED_PLATFORM \
  -e BUILD_SDIST \
  -e WHEEL_OUTPUT_DIR \
  -v "$PWD:/io" \
  -w /io \
  "$IMAGE" \
  bash -euxo pipefail -c '
    git config --global --add safe.directory /io
    git config --global --add safe.directory /io/opencv

    SYSTEM_PYTHON="$(
      find /opt/python -mindepth 2 -maxdepth 2 -type f -path "*/bin/python" \
        | sort | head -n 1
    )"
    test -n "$SYSTEM_PYTHON"
    test -x "$SYSTEM_PYTHON"

    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"

    LATEST_VERSION="$(
      uv python list ">=3.14+freethreaded" \
        --all-versions \
        --only-downloads \
        --output-format json \
      | env -u PYTHON_GIL -u PYTHON_JIT "$SYSTEM_PYTHON" -c "
import json
import re
import sys

data = json.load(sys.stdin)
versions = []
for item in data:
    if item.get(\"implementation\") != \"cpython\":
        continue
    if item.get(\"variant\") != \"freethreaded\":
        continue
    version = item.get(\"version\", \"\")
    if not re.fullmatch(r\"[0-9]+\\.[0-9]+\\.[0-9]+\", version):
        continue
    versions.append(tuple(map(int, version.split(\".\"))))
if not versions:
    raise SystemExit(\"no stable free-threaded CPython found\")
print(\".\".join(map(str, max(versions))))
"
    )"
    test -n "$LATEST_VERSION"
    LATEST_SPEC="${LATEST_VERSION}+freethreaded"
    echo "Latest stable free-threaded candidate: $LATEST_SPEC"

    uv python install "$LATEST_SPEC"
    rm -rf /tmp/venv
    uv venv --python "$LATEST_SPEC" --managed-python --seed /tmp/venv
    export PATH="/tmp/venv/bin:/root/.local/bin:$PATH"
    export PYTHON_JIT=1
    export PYTHON_GIL=0

    python .github/scripts/ci_python_policy.py --verify
    python .github/scripts/ci_opencv_python.py verify-source
    python .github/scripts/ci_opencv_python.py source-manifest \
      --output /tmp/SOURCE_MANIFEST.txt

    python -m pip install --upgrade pip
    python -c "
import subprocess
import sys
import tomllib

with open(\"pyproject.toml\", \"rb\") as handle:
    requirements = tomllib.load(handle)[\"build-system\"][\"requires\"]
subprocess.check_call([
    sys.executable,
    \"-m\",
    \"pip\",
    \"install\",
    *requirements,
    \"wheel\",
    \"twine\",
    \"cmake==4.3.2\",
])
"

    rm -rf /tmp/raw-wheel /tmp/repaired-wheel
    mkdir -p /tmp/raw-wheel /tmp/repaired-wheel

    export CI_BUILD=1
    export ENABLE_CONTRIB=0
    export ENABLE_HEADLESS=1
    export ENABLE_ROLLING=0
    export OPENCV_PYTHON_SKIP_GIT_COMMANDS=1

    python setup.py bdist_wheel \
      --dist-dir /tmp/raw-wheel \
      -v

    AUDITWHEEL="${AUDITWHEEL:-/opt/_internal/pipx/venvs/auditwheel/bin/auditwheel}"
    test -x "$AUDITWHEEL"
    "$AUDITWHEEL" repair /tmp/raw-wheel/*.whl -w /tmp/repaired-wheel

    python .github/scripts/ci_opencv_python.py verify-wheel \
      --dir /tmp/repaired-wheel \
      --expected-platform "$EXPECTED_PLATFORM"

    rm -rf /tmp/verify
    uv venv --python "$LATEST_SPEC" --managed-python --seed /tmp/verify
    PYTHON_JIT=1 PYTHON_GIL=0 \
      /tmp/verify/bin/python .github/scripts/ci_python_policy.py --verify
    /tmp/verify/bin/python -m pip install \
      --no-index \
      --no-deps \
      /tmp/repaired-wheel/*.whl
    PYTHON_JIT=1 PYTHON_GIL=0 \
      /tmp/verify/bin/python .github/scripts/ci_opencv_python.py smoke

    cp /tmp/repaired-wheel/*.whl "/io/$WHEEL_OUTPUT_DIR/"

    if [ "$BUILD_SDIST" = "1" ]; then
      export ENABLE_HEADLESS=0
      rm -rf /tmp/sdist
      mkdir -p /tmp/sdist
      python setup.py sdist \
        --formats=gztar \
        --dist-dir /tmp/sdist
      python .github/scripts/ci_opencv_python.py verify-sdist --dir /tmp/sdist
      python -m twine check /tmp/sdist/*
      cp /tmp/sdist/*.tar.gz /io/ci-dist-sdist/
    fi

    git diff --exit-code
    test -z "$(git status --porcelain --untracked-files=no)"
    chmod -R a+rX "/io/$WHEEL_OUTPUT_DIR"
    if [ "$BUILD_SDIST" = "1" ]; then
      chmod -R a+rX /io/ci-dist-sdist
    fi
  '

git diff --exit-code
test -z "$(git status --porcelain --untracked-files=no)"
