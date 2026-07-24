---
name: Bug report
about: Report a problem with orca-auto
title: "[Bug] "
labels: bug
assignees: ''
---

## Describe the bug

A clear and concise description of what's wrong.

## Steps to reproduce

1. Run '...' (e.g. `orca-auto slice model.stl --profile standard` or a specific web UI action)
2. With settings '...' (relevant `.env` values, if not secret)
3. See error

## Expected behavior

What you expected to happen instead.

## Actual behavior

What actually happened. Include the full error message / stack trace if there is one.

## Logs

```
Paste relevant server logs (uvicorn output) or CLI output here.
```

## Environment

- orca-auto version: `orca-auto --version`
- OS / distro:
- Python version: `python3 --version`
- OrcaSlicer version:
- Running via: [ ] Web UI  [ ] CLI  [ ] `orca-auto u1` (Snapmaker U1 pipeline)
- If this involves the U1 pipeline: Moonraker version / printer firmware, if known

## Additional context

Anything else that might help — job spec JSON (redact filament/color info if irrelevant), profile name, whether this started after an upgrade, etc.
