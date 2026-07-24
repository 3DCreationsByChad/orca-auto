# Headless Slicing Findings & Gotchas (2026-06-11)

Discovered while running a real batch of 211 STLs → A1 mini 0.6 / Generic PETG — on a headless deploy host (`orca-slicer` VM, 192.168.1.51, OrcaSlicer **2.3.1**). 205/211 sliced successfully; the 6 failures were degenerate source meshes (1×1×2 mm, one 12-face cube), not pipeline bugs.

## TL;DR — orca-auto's `slice_file()` is currently broken for headless slicing

`services/slicer.py:slice_file()` runs:

```
OrcaSlicer --load-settings "<process.json>;<machine.json>" --load-filaments "<fil.json>" \
           --slice 0 --export-3mf out.3mf input.stl
```

with **no `--datadir`** and profiles passed as **file paths**. On the deploy host this fails to slice **every** profile (verified by looping 8 of orca-auto's own imported profiles — all "output not created"). Root causes below.

## OrcaSlicer CLI gotchas (the wall)

1. **`--load-settings` does NOT resolve `inherits` from a file path.**
   - A minimal override profile (e.g. `{inherits: "0.30mm Standard @BBL A1M 0.6 nozzle", wall_loops: 6, ...}`) fails with **`unknown config type`** (it also needs a literal `"type": "process"` field).
   - A *system* profile passed by path loads with **default values** (layer 0.2, walls 2), not its real settings — because its own `inherits` chain isn't resolved.

2. **Profile↔printer compatibility is vendor-bundle-driven, not in the files.**
   - Compatibility comes from `resources/profiles/BBL.json` (`process_list` / `machine_list`), NOT from `compatible_printers_condition` in individual profile files (those resolve to `""`).
   - Any profile loaded standalone reads **`process not compatible with printer`** — even after manually flattening the full inheritance chain and setting an explicit `compatible_printers_condition`. `--no-check` does **not** bypass it.

3. **`--datadir` is required but insufficient.**
   - Pointing `--datadir ~/.config/OrcaSlicer` (which holds the system bundle) fixes the `unknown config type` / inherits resolution, but the **compatibility check still fails** for file-loaded process profiles.

4. **Relative-E requires `G92 E0` per layer — even the default config trips it.**
   - The A1 mini uses `use_relative_e_distances = 1`; if `before_layer_change_gcode` lacks `G92 E0`, slicing aborts: *"Relative extruder addressing requires resetting the extruder position at each layer. Add G92 E0 to layer_gcode."* A bare default-config slice hits this too.

5. **Version skew.** The deploy host's CLI is 2.3.1. Project `.3mf` files stamped newer (saw `2.5.0.66`) fail *"File Version not supported"* / *"Param values in 3mf/config error."* Use `--allow-newer-file`, or match versions (the desktop export used to author the source `.3mf` needs to match the headless CLI's version).

## The working recipe (single-color) — proven on 205 parts

Slicing a **project `.3mf`** uses its *embedded* `project_settings.config` and **bypasses the compatibility check**. So:

1. **Source a complete resolved config:** in desktop OrcaSlicer, select the exact profile/machine/filament, **Save Project As `.3mf`**. Its `Metadata/project_settings.config` is a full ~543-key resolved config.
2. **Patch** `before_layer_change_gcode` → `"G92 E0\n"` (fixes gotcha #4 on 2.3.1).
3. **Per STL, build a single-object project `.3mf`** (the U1 `threemf_builder` pattern):
   - mesh via `trimesh.load(stl, force='mesh')` → `3D/Objects/obj_1.model` (`<vertices>`/`<triangles>`)
   - `3D/3dmodel.model`: object id 2 → `<component p:path>` → mesh; `<build><item>` centered on bed (`90,90`, bottom `z=0`)
   - `Metadata/model_settings.config`: object id 2, `extruder=1`
   - the patched `Metadata/project_settings.config`; template `[Content_Types].xml` / `_rels` / `slice_info.config`
4. **Slice:** `OrcaSlicer --datadir ~/.config/OrcaSlicer --arrange 1 --slice 0 --export-3mf out.3mf in.3mf` (`DISPLAY=:99`). ~1.2 s/part.
5. **Extract** the embedded `.gcode` from the output 3mf.

Batch script used: `~/batch/build_and_slice.py` on the deploy host.

## How orca-auto should actually be fixed

- Pass `--datadir` in `slice_file()`.
- Don't slice by loading standalone process/machine profile files (compatibility will fail). Instead either:
  - **(preferred)** slice via a **project `.3mf`** that embeds a complete resolved config (build it like `threemf_builder`), or
  - register presets into the OrcaSlicer user config dir so the bundle establishes compatibility, then drive by selected preset.
- Apply the `G92 E0` patch (or pin a machine profile that sets it) for relative-E printers.
- Default `ORCA_SLICED_PATH` is `/data/output` which doesn't exist on a fresh deploy host — set it explicitly.

## Infra / access gotchas (dev machine → deploy host)

- If the deploy host is only reachable via a jump host (e.g. `ssh user@jump-host` → `ssh slicehost@192.168.1.51`), remember your dev machine's SSH key may not be authorized on the target user directly. File transfer: scp dev-machine → jump-host → target, or pipe through.
- If your file-sync tool (e.g. rclone) mounts remote storage on the deploy host, a `stat` right after a write can throw a transient `Input/output error` while the upload finishes async — the data is usually fine (`unzip -t` or similar confirms).
- If your environment sandboxes `curl`: force `--http1.1` if a proxy breaks HTTP/2 (exit 92), and write via shell `>` redirection rather than `curl -o` if sandboxed `-o` writes get discarded.
- Example public WebDAV listing (Nextcloud or similar): `curl -X PROPFIND --http1.1 -u "<shareToken>:" https://your-cloud-host.example.com/public.php/webdav/ -H "Depth: 1"`.
