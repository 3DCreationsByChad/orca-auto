"""The name a job carries onto the printer's screen."""

import pytest

from orca_api.u1.naming import MAX_STEM, safe_stem


@pytest.mark.parametrize("raw,expected", [
    ("card-3up", "card-3up"),
    ("Quad Lock card", "Quad-Lock-card"),
    ("bracket_clip.v2", "bracket_clip.v2"),
    ("café crème", "caf-cr-me"),
])
def test_keeps_readable_names(raw, expected):
    assert safe_stem(raw) == expected


def test_collapses_runs_of_replacements():
    # "a   b" should not become "a---b" -- three dashes read as noise on a
    # 3-inch screen
    assert safe_stem("a   b") == "a-b"


@pytest.mark.parametrize("attack", [
    "../../etc/passwd",
    "/etc/passwd",
    "a/b/c",
    "..",
    ".",
])
def test_a_job_spec_cannot_escape_the_workdir(attack):
    stem = safe_stem(attack)
    assert "/" not in stem
    assert not stem.startswith(".")
    assert stem not in ("", ".", "..")


def test_empty_and_unusable_names_fall_back():
    assert safe_stem("") == "job"
    assert safe_stem("   ") == "job"
    assert safe_stem("!!!") == "job"
    assert safe_stem(None) == "job"


def test_length_is_capped():
    stem = safe_stem("x" * 200)
    assert len(stem) == MAX_STEM


def test_a_capped_name_does_not_end_in_a_separator():
    stem = safe_stem("x" * (MAX_STEM - 1) + "-tail")
    assert not stem.endswith("-")


def test_the_extension_is_not_part_of_the_stem():
    # callers pass a stem; ".gcode" is appended downstream. A name that already
    # carries one would otherwise print as card.gcode.gcode
    assert safe_stem("card.gcode") == "card"
    assert safe_stem("card.3mf") == "card"


# --- the date the reel argues for: in the title, not in the filesystem -------

from datetime import date

from orca_api.u1.naming import DATE_RE, with_date_suffix


def test_the_suffix_is_month_day_year_in_parens():
    assert with_date_suffix("card-3up", when=date(2026, 8, 13)) == "card-3up(08.13.26)"


def test_the_date_is_zero_padded_so_names_sort_chronologically():
    # deliberate deviation from the reel, which writes 8.12.26. Unpadded,
    # "10.01.26" sorts before "8.13.26" and the whole point of the convention
    # -- a list that reads in order -- is lost.
    early = with_date_suffix("card", when=date(2026, 8, 13))
    late = with_date_suffix("card", when=date(2026, 10, 1))
    assert early < late
    assert early == "card(08.13.26)"


def test_appending_is_idempotent():
    # re-slicing the same job on the same day must not stack suffixes
    once = with_date_suffix("card", when=date(2026, 8, 13))
    assert with_date_suffix(once, when=date(2026, 8, 13)) == once


def test_a_stale_suffix_is_replaced_not_appended():
    old = "card(08.01.26)"
    assert with_date_suffix(old, when=date(2026, 8, 13)) == "card(08.13.26)"


def test_the_base_is_sanitised_before_the_date_is_added():
    stem = with_date_suffix("../../etc/passwd", when=date(2026, 8, 13))
    assert "/" not in stem
    assert stem.endswith("(08.13.26)")


def test_the_total_length_still_respects_the_cap():
    stem = with_date_suffix("x" * 200, when=date(2026, 8, 13))
    assert len(stem) <= MAX_STEM + len("(08.13.26)")
    assert stem.endswith("(08.13.26)")


def test_the_suffix_pattern_matches_what_we_write():
    assert DATE_RE.search(with_date_suffix("card", when=date(2026, 8, 13)))


def test_it_defaults_to_today():
    stem = with_date_suffix("card")
    today = date.today()
    assert stem == f"card({today:%m.%d.%y})"


def test_a_date_suffix_survives_re_sanitising():
    """The pipeline sanitises the name again on the way to disk. Parentheses
    were not in the safe set, so a dated name arrived as card-3up-08.13.26 --
    green tests all round, wrong filename on the printer. Caught by a real
    slice, not by a mock."""
    assert safe_stem("card-3up(08.13.26)") == "card-3up(08.13.26)"
    assert with_date_suffix(with_date_suffix("card", when=date(2026, 8, 13)),
                            when=date(2026, 8, 13)) == "card(08.13.26)"


def test_unbalanced_parens_are_still_harmless():
    stem = safe_stem("weird)name(")
    assert "/" not in stem and stem
