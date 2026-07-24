# Contributing to orca-auto

Thanks for considering a contribution. This project is a FastAPI service + CLI for batch-slicing with OrcaSlicer, plus a Snapmaker U1 slice-coordinator. This guide covers how to get set up, the workflow for changes, and code style expectations.

## Development Setup

1. Fork and clone the repo:
   ```bash
   git clone https://github.com/<your-fork>/orca-auto.git
   cd orca-auto
   ```

2. Run the bootstrap script (creates a venv, installs deps, copies `.env.example` → `.env`):
   ```bash
   ./setup.sh
   source venv/bin/activate
   ```

   Or set up manually:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements-dev.txt
   pip install -e .
   cp .env.example .env
   ```

3. Run the test suite to confirm your environment is working:
   ```bash
   pytest -q
   ```

You do **not** need a real OrcaSlicer binary, an X display, or a Snapmaker U1 to run the default test suite — tests are unit-level and mock the slicer/Moonraker boundaries. Tests marked `hardware` (see `pyproject.toml`'s `markers`) require a real OrcaSlicer install and/or a live Moonraker instance and are deselected by default.

## Project Layout

- `src/orca_api/` — the FastAPI service (web UI + REST API). See `CLAUDE.md` for the full breakdown of `routers/`, `services/`, and `u1/`.
- `src/orca_cli/` — the `orca-auto` CLI, mostly a thin HTTP client over the API, except `u1_cmd.py` which runs the U1 pipeline locally.
- `tests/` — the pytest suite (`tests/u1/` covers the Snapmaker U1 pipeline specifically).
- `pyproject.toml` — package metadata and pytest configuration (`testpaths = ["tests"]`).

Note: `test_slicer.py`, `test_slicer_full.py`, and `test_all_profiles.py` at the repo root are ad-hoc manual scripts (not part of the pytest suite) used for testing against a real OrcaSlicer install with hardcoded paths — they are not run in CI and don't need to pass for a PR to be accepted.

## Branch & PR Workflow

1. Create a branch off `main` with a descriptive name (`fix/slice-timeout`, `feat/u1-batch-jobs`).
2. Make your changes, keeping commits focused and using [Conventional Commits](https://www.conventionalcommits.org/) style (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
3. Add or update tests for any behavior change (see Testing below).
4. Run the full suite before opening a PR:
   ```bash
   pytest -q
   ```
5. Open a PR against `main` with:
   - A summary of what changed and why
   - Note if it touches the U1 pipeline, the setup wizard, or file/path handling (all security-sensitive — see below)
   - Confirmation that `pytest -q` passes

## Testing

- Framework: **pytest** (`pytest-asyncio` for async code — `asyncio_mode = "auto"` in `pyproject.toml`, no decorators needed).
- New code should include unit tests under `tests/` mirroring the module it exercises (e.g. changes to `src/orca_api/u1/pipeline.py` get tests in `tests/u1/test_pipeline.py`).
- Mock the slicer subprocess call and the Moonraker HTTP client rather than shelling out to a real binary or hitting a real printer — see the existing `tests/u1/` tests for the pattern (`httpx.MockTransport`, `monkeypatch` on subprocess calls).
- Run with coverage locally if you want a sense of gaps:
  ```bash
  pytest --cov=src --cov-report=term-missing
  ```

## Code Style

- Python 3.11+, type annotations on function signatures.
- Follow the existing module boundaries: routers stay thin (HTTP concerns only), business logic lives in `services/` or `u1/`.
- Prefer small, focused functions and modules over large ones — the `u1/` package is a good example (each file has one job: build, slice, transport, or validate).
- Use `pathlib.Path` for filesystem paths, not raw strings, in new code.
- No hardcoded secrets, hostnames, or credentials — configuration goes through `ORCA_*` environment variables (see `.env.example` and `CLAUDE.md`).

## Security-Sensitive Areas

Treat these as requiring extra care and a clear PR description of what changed and why:

- **File path handling** (`services/file_browser.py`, the `models_path`/`sliced_path` settings) — this app resolves user-supplied paths against a configured root; changes here should not introduce path traversal.
- **Subprocess invocation** (`services/slicer.py`, `u1/slice.py`) — these shell out to the OrcaSlicer binary; avoid introducing shell string interpolation of user input.
- **Moonraker client** (`u1/moonraker_client.py`) — handles an API key and pushes files to a real printer; be careful with defaults for `--mode` (queue vs. immediate print).

## Reporting Issues

Please use the issue templates under `.github/ISSUE_TEMPLATE/` when filing a bug report or feature request — they ask for the information (OrcaSlicer version, OS, whether the U1 pipeline is involved, etc.) that's usually needed to reproduce a problem.

## Using Claude Code

This repo includes a `CLAUDE.md` with architecture notes, verified commands, and configuration reference. If you're using [Claude Code](https://claude.com/claude-code) to contribute, it will pick this up automatically and should already know how to run the server, the CLI, and the test suite without you re-explaining the project structure.
