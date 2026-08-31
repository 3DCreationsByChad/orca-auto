"""The name a job carries onto the printer's screen.

Every U1 job used to upload as `job.gcode`, so each one overwrote the last and
the machine's file list held exactly one unidentifiable entry. A job now names
itself, and that name reaches the screen unchanged.

The sanitiser is a security boundary as much as a cosmetic one: the stem is
joined onto the working directory and sent as a Moonraker upload filename, so a
job spec must not be able to steer either out of its tree.
"""

from __future__ import annotations

import re
from datetime import date

__all__ = ["MAX_STEM", "FALLBACK_STEM", "DATE_RE", "safe_stem", "with_date_suffix"]

#: Long enough for "quad-lock-card-3up", short enough to read on the U1's screen
#: without truncating to ambiguity.
MAX_STEM = 40

FALLBACK_STEM = "job"

#: Everything outside this is a separator. Deliberately narrow -- the name is
#: consumed by a filesystem, an HTTP multipart filename and an embedded screen.
#: Parentheses are in the set because `with_date_suffix` writes them and the
#: pipeline sanitises the name again on its way to disk: without them a dated
#: job silently landed as `card-3up-08.13.26`. They are safe on every target --
#: no shell runs these names, they travel as a multipart filename.
_UNSAFE = re.compile(r"[^A-Za-z0-9._()-]+")
_RUNS = re.compile(r"-{2,}")

#: Stripped so a name given as a filename does not print as `card.gcode.gcode`.
_KNOWN_SUFFIXES = (".gcode", ".3mf", ".stl", ".json", ".scad")


def safe_stem(raw: str | None) -> str:
    """A filename stem safe to write to disk and to show on the printer.

    Unusable input degrades to `"job"` rather than raising: a bad name should
    never be the reason a real print does not happen.
    """
    text = (raw or "").strip()
    for suffix in _KNOWN_SUFFIXES:
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break

    stem = _RUNS.sub("-", _UNSAFE.sub("-", text)).strip("-")
    # A leading dot hides the file from every listing and turns ".." into a
    # traversal; strip them after separator collapsing so "..foo" survives as
    # "foo" rather than being thrown away.
    stem = stem.lstrip(".")

    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip("-._")

    return stem or FALLBACK_STEM


#: `(MM.DD.YY)` at the end of a stem. Written by us, so the pattern is exact.
DATE_RE = re.compile(r"\((\d{2}\.\d{2}\.\d{2})\)$")


def with_date_suffix(raw: str | None, when: date | None = None) -> str:
    """`card-3up` -> `card-3up(08.13.26)`.

    The export date belongs in the title rather than in the filesystem, for
    reasons that survive leaving the filesystem: file metadata is hidden by some
    file managers, cannot be keyword-searched in others, and does not come along
    when the file is imported somewhere else. Here it also buys history -- a
    re-slice lands beside its predecessor on the printer instead of silently
    replacing it.

    ⚠️ Zero-padded, which the source convention is not. Unpadded, `10.1.26`
    sorts before `8.13.26` and a list that reads in order is the whole point.

    Same-day re-slices deliberately collide: that is one file per design per
    day, not one per slice.
    """
    # Strip any existing suffix *before* sanitising -- parentheses are not in
    # the safe set, so `card(08.01.26)` would already have become
    # `card-08.01.26` and the pattern would never match. Replace rather than
    # append, so a name does not accumulate one date per pass.
    stem = safe_stem(DATE_RE.sub("", (raw or "").strip()))
    return f"{stem}({(when or date.today()):%m.%d.%y})"
