"""Tests for resolving real OrcaSlicer filament presets from the vendor bundle.

The U1 pipeline embeds a *resolved* config in the project .3mf (it deliberately
bypasses preset resolution so the vendor compatibility check can't reject it).
That means every thermal/flow value has to be spliced in by us -- naming a
preset in `filament_settings_id` does nothing on its own.
"""

import json

import pytest

from orca_api.u1.filament_presets import (
    FilamentPresetError,
    resolve_filament_preset,
)


def _write_bundle(datadir, vendor="Snapmaker", profiles=None):
    """Create a minimal <datadir>/system/<vendor>/filament/*.json bundle."""
    fdir = datadir / "system" / vendor / "filament"
    fdir.mkdir(parents=True, exist_ok=True)
    for name, body in (profiles or {}).items():
        (fdir / f"{name}.json").write_text(json.dumps(body))
    return fdir


def test_resolves_inherits_chain_with_child_overriding_parent(tmp_path):
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1 base": {
            "type": "filament",
            "name": "Snapmaker PLA @U1 base",
            "filament_type": ["PLA"],
            "nozzle_temperature": ["220"],
            "hot_plate_temp": ["65"],
        },
        "Snapmaker PLA @U1": {
            "type": "filament",
            "name": "Snapmaker PLA @U1",
            "inherits": "Snapmaker PLA @U1 base",
            "hot_plate_temp": ["55"],
        },
    })

    preset = resolve_filament_preset("Snapmaker PLA @U1", tmp_path)

    assert preset["nozzle_temperature"] == ["220"]  # inherited from parent
    assert preset["hot_plate_temp"] == ["55"]       # child wins
    assert preset["filament_type"] == ["PLA"]


def test_strips_preset_metadata_keys(tmp_path):
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1": {
            "type": "filament",
            "name": "Snapmaker PLA @U1",
            "from": "system",
            "setting_id": "11953139350",
            "instantiation": "true",
            "compatible_printers": ["Snapmaker U1 (0.4 nozzle)"],
            "nozzle_temperature": ["220"],
        },
    })

    preset = resolve_filament_preset("Snapmaker PLA @U1", tmp_path)

    assert preset == {"nozzle_temperature": ["220"]}


def test_bare_material_name_resolves_to_the_u1_profile(tmp_path):
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1": {"name": "Snapmaker PLA @U1", "nozzle_temperature": ["220"]},
        "Snapmaker PLA": {"name": "Snapmaker PLA", "nozzle_temperature": ["999"]},
    })

    preset = resolve_filament_preset("PLA", tmp_path)

    assert preset["nozzle_temperature"] == ["220"]


def test_unknown_filament_raises_and_lists_what_is_available(tmp_path):
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1": {"name": "Snapmaker PLA @U1"},
        "Snapmaker ASA @U1": {"name": "Snapmaker ASA @U1"},
    })

    with pytest.raises(FilamentPresetError) as exc:
        resolve_filament_preset("UNOBTANIUM", tmp_path)

    msg = str(exc.value)
    assert "UNOBTANIUM" in msg
    assert "Snapmaker ASA @U1" in msg  # tells the user what they *can* pick


def test_missing_parent_in_inherits_chain_raises(tmp_path):
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1": {"name": "Snapmaker PLA @U1", "inherits": "does not exist"},
    })

    with pytest.raises(FilamentPresetError, match="does not exist"):
        resolve_filament_preset("Snapmaker PLA @U1", tmp_path)


def test_inherits_cycle_raises_instead_of_hanging(tmp_path):
    _write_bundle(tmp_path, profiles={
        "A": {"name": "A", "inherits": "B"},
        "B": {"name": "B", "inherits": "A"},
    })

    with pytest.raises(FilamentPresetError, match="[Cc]ycle"):
        resolve_filament_preset("A", tmp_path)


def test_resolve_preset_name_returns_the_concrete_profile_name(tmp_path):
    from orca_api.u1.filament_presets import resolve_preset_name
    _write_bundle(tmp_path, profiles={
        "Snapmaker PLA @U1": {"name": "Snapmaker PLA @U1"},
    })

    assert resolve_preset_name("PLA", tmp_path) == "Snapmaker PLA @U1"
