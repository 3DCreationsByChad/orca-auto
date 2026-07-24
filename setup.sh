#!/usr/bin/env bash
set -euo pipefail

# orca-auto — First-time setup
# Usage: ./setup.sh

echo "=== orca-auto Setup ==="

# Check prerequisites
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 is required."; exit 1; }

PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Found Python ${PYTHON_VERSION}"

if ! command -v orcaslicer >/dev/null 2>&1; then
  echo ""
  echo "Warning: 'orcaslicer' was not found on PATH."
  echo "  orca-auto needs a real OrcaSlicer binary to slice files."
  echo "  Set ORCA_ORCASLICER_BIN in .env to its full path once installed"
  echo "  (see README.md's 'Installation Prerequisites' section)."
fi

if ! command -v openscad >/dev/null 2>&1; then
  echo ""
  echo "Note: 'openscad' was not found on PATH (optional)."
  echo "  Only required if you use the SCAD-to-STL pipeline."
  echo "  Set ORCA_OPENSCAD_BIN in .env if you install it later."
fi

# Virtual environment
if [ ! -d venv ]; then
  echo ""
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

# Environment
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit it with your values"
fi

# Dependencies
echo ""
echo "Installing dependencies..."
pip install --upgrade pip >/dev/null
pip install -r requirements-dev.txt
pip install -e .

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your ORCA_ORCASLICER_BIN and paths"
echo "  2. Activate the venv: source venv/bin/activate"
echo "  3. Run: uvicorn orca_api.main:app --host 0.0.0.0 --port 8000"
echo "  4. Open: http://localhost:8000/ui"
echo "  5. Using Claude Code? CLAUDE.md has all the context."
