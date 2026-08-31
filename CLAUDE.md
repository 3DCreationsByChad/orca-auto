# orca-auto

**Version:** 0.1.0 | **Port:** 8000 | **Stack:** Python 3.11+ / FastAPI / Jinja2 / argparse CLI

## What

Batch-slicing automation for OrcaSlicer. A FastAPI web UI + CLI headlessly slices STL/3MF/SCAD files against your OrcaSlicer profiles, with a background job queue and an optional Snapmaker U1 slice-coordinator that builds multicolor `.3mf` projects and pushes G-code to a printer's Moonraker instance.

## Quick Start

```bash
./setup.sh                                       # First-time setup
source venv/bin/activate
uvicorn orca_api.main:app --host 0.0.0.0 --port 8000   # Start the API + web UI
pytest -q                                        # Run tests
```

## Commands

```bash
# Development
python3 -m venv venv && source venv/bin/activate   # Create/activate venv
pip install -e .                                    # Install package (editable)
pip install -r requirements-dev.txt                 # Install + dev/test deps
uvicorn orca_api.main:app --reload --port 8000      # Dev server (autoreload)

# Testing
pytest -q                                           # Run test suite (tests/, 232 tests)
pytest -q -m "not hardware"                         # Skip tests needing real OrcaSlicer/Moonraker (default)
pytest --cov=src --cov-report=term-missing          # With coverage

# CLI (thin client over the running API)
orca-auto setup                                     # Interactive first-run config wizard
orca-auto slice <path> --profile <name>              # Queue a slice job
orca-auto list [path]                               # Browse files under models root
orca-auto jobs [--limit N]                          # Recent job status
orca-auto profiles                                  # List available slice profiles
orca-auto status                                    # API health check
orca-auto config [--url URL]                        # View/set CLI's API URL

# Snapmaker U1 (local, host-side — does not go through the API)
orca-auto u1 slice  <job.json> --out job.gcode      # Build .3mf + slice, no push
orca-auto u1 print  <job.json> --moonraker <url>    # Build + slice + push to Moonraker
```

## Architecture

```
src/orca_api/          # FastAPI service (the web UI + REST API)
├── main.py            # App factory, lifespan (job queue worker), setup-redirect middleware
├── config.py          # Pydantic Settings (ORCA_* env vars), is_configured check
├── models.py          # Pydantic request/response models
├── routers/           # files, jobs, pipeline, profiles, scad, slice, ui
├── services/          # slicer, job_queue, profile_import, scad_parser/exporter, file_browser
├── templates/         # Jinja2 HTML (setup wizard, index, SCAD editor, pipeline)
└── u1/                # Snapmaker U1 slice-coordinator (see below)
    ├── threemf_builder.py   # Assemble a multicolor project .3mf from STL + tool assignments
    ├── slice.py              # Headless OrcaSlicer CLI wrapper (project-.3mf recipe)
    ├── moonraker_client.py   # Async httpx client: upload/enqueue/start/status
    ├── tool_map.py            # 4-tool (U1) color/filament assignment validation
    ├── filament_presets.py    # Resolve real OrcaSlicer filament presets from the vendor bundle
    ├── loaded_filament.py     # What is physically loaded per tool, via the spool RFID tags
    ├── tool_resolution.py     # Resolve tools by loaded material/color instead of slot number
    ├── naming.py              # Job naming (readable stems + date suffix) on the printer screen
    ├── scad_parts.py          # Render a job spec's .scad parts to STL before building
    ├── support.py             # Support body / support interface material designation
    ├── thumbnail.py           # Render + splice G-code preview thumbnails for the U1 screen
    ├── timelapse.py           # Client-side webcam timelapse capture during a print
    └── pipeline.py            # Composes the above: build -> slice -> (optionally) push

src/orca_cli/          # Argparse CLI
├── cli.py             # Subcommands; thin HTTP client over the API (slice/list/jobs/etc.)
├── client.py           # OrcaClient — httpx wrapper used by cli.py
├── config.py           # CLI's own config file (~/.config/orca-auto/config.json)
├── setup.py             # Interactive setup wizard (terminal equivalent of /ui/setup)
└── u1_cmd.py            # `orca-auto u1` subcommands — calls orca_api.u1.pipeline directly

tests/                 # pytest suite (unit + u1/ subpackage), testpaths = tests/
docs/                  # headless-slicing findings, setup wizard screenshot
```

The web UI and most CLI commands (`slice`, `list`, `jobs`, `profiles`, `status`) talk to the FastAPI service over HTTP — the CLI is a thin client, so `orca-auto` needs a running API (`get_api_url()`, default `http://localhost:8000`). The **U1 command group is the one exception**: `orca-auto u1 slice|print` runs the build→slice→push pipeline directly on the machine you invoke it from (no API call), because slicing and the Moonraker push are host-local operations tied to a real OrcaSlicer binary and (for `print`) network access to the printer.

## Key Files

```
src/orca_api/main.py              FastAPI app, lifespan, setup-redirect middleware
src/orca_api/config.py            Settings + is_configured() gate for the setup wizard
src/orca_api/services/slicer.py   OrcaSlicer CLI wrapper for the standard (non-U1) slice path
src/orca_api/services/job_queue.py  Background job queue, persisted to ORCA_JOBS_FILE
src/orca_api/u1/pipeline.py       build_and_slice() / build_slice_push() — the U1 flow
src/orca_api/u1/slice.py          Headless OrcaSlicer recipe (see docs/headless-slicing-findings-*.md)
src/orca_cli/cli.py               argparse entry point, registered as the `orca-auto` script
src/orca_cli/u1_cmd.py            `orca-auto u1` subcommands (job spec -> U1Part list -> pipeline)
pyproject.toml                    Package metadata, pytest config (testpaths, markers)
.env.example                      All ORCA_* configuration variables with defaults
docs/headless-slicing-findings-2026-06-11.md   Why the U1 slicer uses the project-.3mf trick
```

## Configuration

All configuration is via environment variables (prefix `ORCA_`), loaded from `.env`. See `.env.example`:

| Variable | Required | Description |
|----------|----------|--------------|
| `ORCA_MODELS_PATH` | No (default `/data/models`) | Root directory for the STL file browser |
| `ORCA_SLICED_PATH` | No (default `/data/output`) | Where sliced output files are saved |
| `ORCA_PROFILES_PATH` | No (default `/opt/orcaslicer/profiles`) | Path to OrcaSlicer profiles |
| `ORCA_ORCASLICER_BIN` | Yes, to actually slice | Path to the OrcaSlicer binary |
| `ORCA_OPENSCAD_BIN` | No, only for SCAD pipeline | Path to the OpenSCAD binary (SCAD-to-STL export) |
| `ORCA_JOBS_FILE` | No (default `./data/jobs.json`) | Job queue persistence file |
| `ORCA_API_HOST` | No (default `0.0.0.0`) | API bind host |
| `ORCA_API_PORT` | No (default `8000`) | API bind port |
| `ORCA_DEBUG` | No (default `false`) | Debug logging |

The app also has a first-run **setup wizard** (`/ui/setup` in the browser, or `orca-auto setup` on the CLI) that writes these values and imports printer profiles interactively — you don't have to hand-edit `.env` for the standard (non-U1) path. The U1 command group has no wizard; its `--bin`/`--datadir`/`--moonraker`/`--api-key` flags are passed explicitly per invocation (see `orca-auto u1 slice --help` / `orca-auto u1 print --help`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
