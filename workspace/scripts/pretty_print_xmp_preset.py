#!/usr/bin/env python3
"""
Extract `comfy:presetJson` from an XMP sidecar and pretty-print it.

Why:
- In XMP it's stored as an XML attribute, so you'll see entity escapes (&quot; etc).
- This script unescapes and formats it for human inspection.
"""

from __future__ import annotations

import argparse
import html
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Pretty-print comfy:presetJson from XMP")
    ap.add_argument("xmp", help="Path to .xmp/.XMP file")
    ap.add_argument(
        "--namespace",
        default="https://ys2n.com/ns/comfyui/1.0/",
        help="ComfyUI XMP namespace URI (default: https://ys2n.com/ns/comfyui/1.0/)",
    )
    ap.add_argument("--out", default="", help="Write JSON to this file (default: print)")
    args = ap.parse_args()

    xmp_path = Path(args.xmp)
    if not xmp_path.exists():
        ap.error(f"Not found: {xmp_path}")

    tree = ET.parse(xmp_path)
    root = tree.getroot()
    ns = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
    desc = root.find(".//rdf:Description", ns)
    if desc is None:
        raise SystemExit("No rdf:Description found in XMP")

    key = f"{{{args.namespace}}}presetJson"
    raw = desc.get(key)
    if not raw:
        raise SystemExit("No comfy:presetJson attribute found")

    unescaped = html.unescape(raw)
    obj = json.loads(unescaped)
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(pretty, encoding="utf-8")
        print(str(out_path))
    else:
        print(pretty)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

