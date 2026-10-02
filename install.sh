#!/usr/bin/env bash
# install.sh — set up J-Rock Agent
#
# Notes on why this script is fussy about the interpreter:
#
#   * The project needs CPython >= 3.12. Older versions are missing wheels for
#     modern numpy/pandas, and pip then tries to build them from source, which
#     fails on aarch64/ARM without a toolchain.
#   * If `uv` is available it is preferred: it can fetch a known-good CPython
#     for your platform so you are not at the mercy of the system interpreter.
#   * A pre-existing .venv that lacks pip is reused, not clobbered.
#
set -euo pipefail

PY_MIN_MAJOR=3
PY_MIN_MINOR=12
VENV_DIR="${VENV_DIR:-.venv312}"

echo "==> Creating directories..."
mkdir -p data logs

find_python() {
    # A system CPython that is new enough.
    for cand in python3.13 python3.12 python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)" 2>/dev/null; then
                echo "$cand"
                return 0
            fi
        fi
    done
    return 1
}

if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
    echo "==> Reusing existing virtualenv at $VENV_DIR"
else
    if command -v uv >/dev/null 2>&1; then
        echo "==> Creating virtualenv with uv (CPython $PY_MIN_MAJOR.$PY_MIN_MINOR+)..."
        # --seed puts pip inside the venv, which a plain `uv venv` does not do.
        uv venv --python "$PY_MIN_MAJOR.$PY_MIN_MINOR" --seed "$VENV_DIR"
    else
        PY="$(find_python || true)"
        if [ -z "${PY:-}" ]; then
            echo "ERROR: need CPython >= $PY_MIN_MAJOR.$PY_MIN_MINOR on PATH."
            echo "       Install a newer Python, or install 'uv' (https://docs.astral.sh/uv/)."
            exit 1
        fi
        echo "==> Creating virtualenv with $PY -m venv..."
        "$PY" -m venv "$VENV_DIR"
    fi
fi

PYBIN="$VENV_DIR/bin/python"
if [ ! -x "$PYBIN" ]; then
    echo "ERROR: $PYBIN not found — virtualenv creation failed."
    exit 1
fi

echo "==> Interpreter: $("$PYBIN" -V 2>&1) at $PYBIN"

# A venv without pip (common when created by `uv venv` without --seed).
if ! "$PYBIN" -m pip --version >/dev/null 2>&1; then
    echo "==> Bootstrapping pip into the virtualenv..."
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$PYBIN" pip
    else
        "$PYBIN" -m ensurepip --upgrade
    fi
fi

echo "==> Installing dependencies..."
# Upgrade pip only if it is usable but old; never fail the whole install on a
# transient network blip, since the pinned deps are what actually matter.
"$PYBIN" -m pip install --upgrade pip || echo "    (pip upgrade skipped — offline?)"
"$PYBIN" -m pip install -r requirements.txt

echo "==> Verifying the trading engine imports..."
"$PYBIN" -c "import trader.strategies, trader.indicators" \
    && echo "    ok" \
    || { echo "    FAILED — run '.venv312/bin/python -m trader.selfcheck' for detail"; exit 1; }

echo "==> Setting up .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    Created .env from .env.example — edit it before running!"
else
    echo "    .env already exists, skipping."
fi

cat <<EOF

Done. To start:
    source $VENV_DIR/bin/activate
    python run.py

Sanity check (indicators + all 10 strategies):
    $VENV_DIR/bin/python -m trader.selfcheck
EOF
