## Media fixtures for integration tests

Put sample **ComfyUI-saved** media files here to exercise end-to-end roundtripping:

- Add a `*.mp4` that has embedded ComfyUI metadata (`prompt`/`workflow` tags via ffprobe)
- Add its companion `*.png` with the **same stem** (e.g. `foo.mp4` + `foo.png`)

Alternatively, you can list workspace-relative sample paths in `tests/fixtures/media/manifest.json`.
This lets you test against real local `output/output/wip/...` files without committing large binaries.
If the manifest references files that are not present on the current machine, the integration test will emit a **warning** listing the missing paths and skip those entries.

The integration test `tests/test_integration_media_roundtrip.py` will:
- copy the fixtures into a temporary directory
- run `scripts/process_wip_dir.py` to generate sidecars (`.preset.json`, `.metadata.json`, `.workflow.json`, `.template.cleaned.json`, `.XMP`)
- run `scripts/check_roundtrip_dir.py` to verify that:
  - embedded MP4 metadata matches the sidecars
  - `preset.json` can be applied back onto `template.cleaned.json` and all fields land correctly

Notes:
- The test will **skip** if no fixtures are present or if `ffprobe` is not installed.
- Keep fixtures small when possible (but real-world samples are fine).

