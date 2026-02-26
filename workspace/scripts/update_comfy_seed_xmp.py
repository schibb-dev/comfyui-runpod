#!/usr/bin/env python3
"""
Update (merge into) an existing XMP sidecar with ComfyUI seed metadata.

Design goals
- Safe merge: do NOT delete or rewrite existing custom metadata fields.
- Only set/overwrite fields in our custom namespace (comfy:*).
- Extract seed(s) from a ComfyUI/VHS-saved MP4 (ffprobe container tags) or PNG (tEXt/iTXt chunks).

Typical usage:
  python scripts/update_comfy_seed_xmp.py "path/to/video.mp4"
  python scripts/update_comfy_seed_xmp.py "path/to/image.png" --xmp "path/to/sidecar.XMP"

Notes
- For your workflows, the "used seed" is typically RandomNoise.inputs.noise_seed (because SamplerCustomAdvanced uses RandomNoise).
- Many MP4s embed ComfyUI metadata in container tags named "prompt" and "workflow".
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import xml.etree.ElementTree as ET

from comfy_meta_lib import (
    collect_seeds_from_prompt,
    extract_preset,
    extract_prompt_workflow_from_png_chunks,
    extract_prompt_workflow_from_tags,
    ffprobe_format_tags,
    json_min,
    read_png_text_chunks,
    stable_json_sha256,
)


DEFAULT_COMFY_NS = "https://ys2n.com/ns/comfyui/1.0/"
COMFY_NS = DEFAULT_COMFY_NS
XMLNS_NS = "http://www.w3.org/2000/xmlns/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMPMETA_NS = "adobe:ns:meta/"
X_NS = "adobe:ns:meta/"
XMP_NS = "http://ns.adobe.com/xap/1.0/"

def _workflow_id(workflow_obj: Any) -> Optional[str]:
    if isinstance(workflow_obj, dict):
        v = workflow_obj.get("id")
        if isinstance(v, str) and v:
            return v
    return None


def _resolve_xmp_path(media_path: Path, xmp_arg: Optional[str]) -> Path:
    if xmp_arg:
        return Path(xmp_arg)
    # Prefer existing sidecar if present, preserving case.
    cand1 = media_path.with_suffix(".XMP")
    cand2 = media_path.with_suffix(".xmp")
    if cand1.exists():
        return cand1
    if cand2.exists():
        return cand2
    # Otherwise create .XMP by default (matches your example).
    return cand1


def _ensure_minimal_xmp_tree() -> ET.ElementTree:
    # Build:
    # <x:xmpmeta xmlns:x="adobe:ns:meta/">
    #   <rdf:RDF xmlns:rdf="...">
    #     <rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/"/>
    #   </rdf:RDF>
    # </x:xmpmeta>
    xmpmeta = ET.Element(f"{{{XMPMETA_NS}}}xmpmeta", {f"{{{XMPMETA_NS}}}xmptk": "ComfySeedXMP"})
    rdf = ET.SubElement(xmpmeta, f"{{{RDF_NS}}}RDF")
    ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
    return ET.ElementTree(xmpmeta)


def _load_or_create_xmp(xmp_path: Path) -> ET.ElementTree:
    if xmp_path.exists():
        try:
            return ET.parse(xmp_path)
        except ET.ParseError:
            # Attempt repair for a common invalid pattern:
            # binding a prefix to the reserved XMLNS namespace URI.
            raw = xmp_path.read_text(encoding="utf-8", errors="replace")
            bad_prefixes = re.findall(r'xmlns:([A-Za-z_][\w\.-]*)="http://www\.w3\.org/2000/xmlns/"', raw)
            fixed = raw
            for p in bad_prefixes:
                fixed = re.sub(
                    rf'\s+xmlns:{re.escape(p)}="http://www\.w3\.org/2000/xmlns/"',
                    "",
                    fixed,
                )
                # Remove any attributes that use the now-removed prefix
                fixed = re.sub(rf'\s+{re.escape(p)}:[A-Za-z_][\w\.-]*="[^"]*"', "", fixed)
            # Parse from the repaired string.
            root = ET.fromstring(fixed)
            return ET.ElementTree(root)
    return _ensure_minimal_xmp_tree()


def _get_first_rdf_description(tree: ET.ElementTree) -> ET.Element:
    root = tree.getroot()
    desc = root.find(f".//{{{RDF_NS}}}Description")
    if desc is None:
        # If the file is odd, create missing nodes under root.
        rdf = root.find(f".//{{{RDF_NS}}}RDF")
        if rdf is None:
            rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
        desc = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
    return desc


def update_xmp_in_place(xmp_path: Path, comfy_fields: Dict[str, str]) -> None:
    # Register namespaces to keep output readable.
    ET.register_namespace("x", X_NS)
    ET.register_namespace("rdf", RDF_NS)
    ET.register_namespace("xmp", XMP_NS)
    ET.register_namespace("comfy", COMFY_NS)

    tree = _load_or_create_xmp(xmp_path)
    desc = _get_first_rdf_description(tree)

    # Do NOT set explicit xmlns:* attributes ourselves (ElementTree can emit weird
    # "xmlns namespace as a prefix" artifacts). Registering the namespace above
    # is enough for stable output. If a prior run wrote an xmlns attribute into
    # the tree, remove it.
    xmlns_key = f"{{{XMLNS_NS}}}comfy"
    if xmlns_key in desc.attrib:
        del desc.attrib[xmlns_key]

    # If this XMP was previously written with a different comfy namespace URI,
    # remove our known comfy fields from any namespace before writing them with
    # the current COMFY_NS. This prevents duplicate/ambiguous values while still
    # preserving unrelated custom metadata.
    known_fields = {
        "usedSeed",
        "seedSource",
        "noiseSeed",
        "ksamplerSeed",
        "workflowId",
        "promptSha256",
        "workflowSha256",
        "presetSha256",
        "templateSha256",
        "presetJson",
    }
    to_delete: List[str] = []
    for attr_key in desc.attrib.keys():
        if attr_key.startswith("{") and "}" in attr_key:
            ns_uri, local = attr_key[1:].split("}", 1)
            if local in known_fields:
                to_delete.append(attr_key)
    for k in to_delete:
        try:
            del desc.attrib[k]
        except KeyError:
            pass

    # Only set our fields; do not touch other namespaces.
    for k, v in comfy_fields.items():
        desc.set(f"{{{COMFY_NS}}}{k}", v)

    xmp_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(xmp_path, encoding="UTF-8", xml_declaration=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge ComfyUI used seed into XMP sidecar")
    ap.add_argument("media", help="MP4 (preferred) or PNG with embedded ComfyUI metadata")
    ap.add_argument("--xmp", default="", help="Path to XMP sidecar to update (default: alongside media)")
    ap.add_argument("--namespace", default=DEFAULT_COMFY_NS, help="Custom namespace URI to use for comfy:* fields")
    ap.add_argument(
        "--preset-path",
        default="",
        help="Optional path to a separate preset JSON file (stored in comfy:presetPath)",
    )
    ap.add_argument(
        "--embed-preset-json",
        action="store_true",
        help="Embed comfy:presetJson into XMP (not recommended; can be hard to read).",
    )
    args = ap.parse_args()

    global COMFY_NS
    COMFY_NS = args.namespace

    media_path = Path(args.media)
    if not media_path.exists():
        ap.error(f"Media not found: {media_path}")

    xmp_path = _resolve_xmp_path(media_path, args.xmp or None)

    prompt_obj: Optional[Any] = None
    workflow_obj: Optional[Any] = None

    ext = media_path.suffix.lower()
    if ext == ".png":
        chunks = read_png_text_chunks(media_path)
        prompt_obj, workflow_obj = extract_prompt_workflow_from_png_chunks(chunks)
    else:
        tags = ffprobe_format_tags(media_path)
        prompt_obj, workflow_obj = extract_prompt_workflow_from_tags(tags)

    seeds = collect_seeds_from_prompt(prompt_obj)
    wid = _workflow_id(workflow_obj)
    prompt_hash = stable_json_sha256(prompt_obj) if prompt_obj is not None else None
    workflow_hash = stable_json_sha256(workflow_obj) if workflow_obj is not None else None
    preset_obj = extract_preset(prompt_obj)
    preset_json = json_min(preset_obj) if preset_obj is not None else None
    preset_hash = stable_json_sha256(preset_obj) if preset_obj is not None else None

    comfy_fields: Dict[str, str] = {}
    if seeds.get("used_seed") is not None:
        comfy_fields["usedSeed"] = str(seeds["used_seed"])
    if seeds.get("seed_source"):
        comfy_fields["seedSource"] = str(seeds["seed_source"])
    if seeds.get("noise_seeds"):
        # Store the primary noise seed as a dedicated field for your workflows.
        comfy_fields["noiseSeed"] = str(seeds["noise_seeds"][0])
    if seeds.get("ksampler_seeds"):
        comfy_fields["ksamplerSeed"] = str(seeds["ksampler_seeds"][0])
    if wid:
        comfy_fields["workflowId"] = wid
    if prompt_hash:
        comfy_fields["promptSha256"] = prompt_hash
    if workflow_hash:
        comfy_fields["workflowSha256"] = workflow_hash
    if preset_hash:
        comfy_fields["presetSha256"] = preset_hash
    if args.preset_path:
        comfy_fields["presetPath"] = args.preset_path

    # Only embed the preset JSON when explicitly requested.
    if args.embed_preset_json:
        comfy_fields["presetJson"] = preset_json or ""

    if not comfy_fields:
        raise SystemExit(
            "No ComfyUI seed/workflow data found in media. "
            "Is this file saved with metadata (VHS save_metadata=true), or do you need to use the companion PNG?"
        )

    update_xmp_in_place(xmp_path, comfy_fields)
    print(str(xmp_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

