"""Tests for rendering a job spec's .scad parts to STL."""

import os
import struct
import time

import pytest

from orca_api.u1.scad_parts import (
    ScadRenderError,
    is_stale,
    output_name,
    render_scad,
    scad_dependencies,
)


def _stub_openscad(tmp_path, body):
    """A fake `openscad` that runs `body` (a shell snippet) instead of CGAL."""
    script = tmp_path / "fake-openscad"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    return str(script)


def _binary_stl(facets: int) -> bytes:
    return b"\0" * 80 + struct.pack("<I", facets) + b"\0" * (50 * facets)


# --- dependency graph ------------------------------------------------------


def test_dependencies_include_the_entry_file(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    assert scad_dependencies(entry) == [entry.resolve()]


def test_dependencies_follow_include_and_use(tmp_path):
    (tmp_path / "common.scad").write_text("use <qr.scad>\nmodule c(){}")
    (tmp_path / "qr.scad").write_text("QR_N = 41;")
    entry = tmp_path / "base.scad"
    entry.write_text("include <common.scad>\nc();")
    found = {p.name for p in scad_dependencies(entry)}
    assert found == {"base.scad", "common.scad", "qr.scad"}


def test_dependencies_survive_an_include_cycle(tmp_path):
    (tmp_path / "a.scad").write_text("include <b.scad>")
    (tmp_path / "b.scad").write_text("include <a.scad>")
    assert len(scad_dependencies(tmp_path / "a.scad")) == 2


def test_dependencies_skip_includes_that_do_not_resolve(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("include <MCAD/gears.scad>\ncube(1);")
    assert scad_dependencies(entry) == [entry.resolve()]


# --- staleness -------------------------------------------------------------


def test_missing_output_is_stale(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    assert is_stale(tmp_path / "a.stl", [entry]) is True


def test_empty_output_is_stale(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    out = tmp_path / "a.stl"
    out.write_bytes(b"")
    assert is_stale(out, [entry]) is True


def test_output_newer_than_every_dependency_is_fresh(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    out = tmp_path / "a.stl"
    out.write_bytes(_binary_stl(1))
    os.utime(out, (time.time() + 10, time.time() + 10))
    assert is_stale(out, [entry]) is False


def test_an_edit_to_an_included_file_makes_it_stale(tmp_path):
    """The whole point: entry files are tiny, the geometry lives in the includes."""
    common = tmp_path / "common.scad"
    common.write_text("module c(){cube(1);}")
    entry = tmp_path / "a.scad"
    entry.write_text("include <common.scad>\nc();")
    out = tmp_path / "a.stl"
    out.write_bytes(_binary_stl(1))
    os.utime(out, (time.time() + 10, time.time() + 10))
    assert is_stale(out, scad_dependencies(entry)) is False

    os.utime(common, (time.time() + 20, time.time() + 20))
    assert is_stale(out, scad_dependencies(entry)) is True


# --- output naming ---------------------------------------------------------


def test_output_name_without_params_is_the_bare_stem():
    assert output_name("/x/card-base.scad", None) == "card-base.stl"


def test_different_params_get_different_files():
    a = output_name("/x/card.scad", {"FACE_DOWN": True})
    b = output_name("/x/card.scad", {"FACE_DOWN": False})
    assert a != b and a.startswith("card-") and a.endswith(".stl")


def test_param_order_does_not_change_the_name():
    assert output_name("/x/c.scad", {"A": 1, "B": 2}) == output_name("/x/c.scad", {"B": 2, "A": 1})


# --- rendering -------------------------------------------------------------


def test_render_writes_the_stl_and_passes_params(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    out = tmp_path / "out" / "a.stl"
    argfile = tmp_path / "args.txt"
    stl = _binary_stl(12).hex()
    fake = _stub_openscad(
        tmp_path,
        f'echo "$@" > {argfile}\n'
        f'for a in "$@"; do case "$a" in *.stl) printf "{stl}" | xxd -r -p > "$a";; esac; done',
    )
    render_scad(entry, out, params={"FACE_DOWN": True}, openscad_bin=fake)
    assert out.is_file()
    assert "-D FACE_DOWN=true" in argfile.read_text()


def test_render_is_skipped_when_the_stl_is_current(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    out = tmp_path / "a.stl"
    out.write_bytes(_binary_stl(1))
    os.utime(out, (time.time() + 10, time.time() + 10))
    fake = _stub_openscad(tmp_path, "exit 1")  # would fail if it ran
    assert render_scad(entry, out, openscad_bin=fake) == out


def test_force_re_renders_a_current_stl(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    out = tmp_path / "a.stl"
    out.write_bytes(_binary_stl(1))
    os.utime(out, (time.time() + 10, time.time() + 10))
    fake = _stub_openscad(tmp_path, "exit 3")
    with pytest.raises(ScadRenderError, match="rc=3"):
        render_scad(entry, out, openscad_bin=fake, force=True)


def test_empty_geometry_is_a_failure_not_a_silent_pass(tmp_path):
    """OpenSCAD exits 0 on a non-3D top level object and writes a facet-less STL."""
    entry = tmp_path / "a.scad"
    entry.write_text("include <missing.scad>\nc();")
    out = tmp_path / "a.stl"
    stl = _binary_stl(0).hex()
    fake = _stub_openscad(
        tmp_path,
        'echo "Current top level object is not a 3D object." >&2\n'
        f'for a in "$@"; do case "$a" in *.stl) printf "{stl}" | xxd -r -p > "$a";; esac; done',
    )
    with pytest.raises(ScadRenderError, match="empty geometry"):
        render_scad(entry, out, openscad_bin=fake)
    assert not out.exists()  # a useless STL must not be left behind to look fresh


def test_missing_source_is_reported(tmp_path):
    with pytest.raises(ScadRenderError, match="not found"):
        render_scad(tmp_path / "nope.scad", tmp_path / "o.stl")


def test_missing_openscad_is_reported(tmp_path):
    entry = tmp_path / "a.scad"
    entry.write_text("cube(1);")
    with pytest.raises(ScadRenderError, match="binary not found"):
        render_scad(entry, tmp_path / "o.stl", openscad_bin="/nonexistent/openscad")
