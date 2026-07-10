#!/usr/bin/env bash
# Build docs/DISPOSITION_BUCKET_MODEL.pdf from docs/DISPOSITION_BUCKET_MODEL.md
# Renders mermaid diagrams to SVG, then HTML → PDF via headless Chromium (or pandoc if available).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MD_SRC="${ROOT}/docs/DISPOSITION_BUCKET_MODEL.md"
DIAG_DIR="${ROOT}/docs/diagrams/bucket-model"
BUILD_DIR="${ROOT}/docs/.build/bucket-model"
PDF_OUT="${ROOT}/docs/DISPOSITION_BUCKET_MODEL.pdf"
HTML_OUT="${BUILD_DIR}/DISPOSITION_BUCKET_MODEL.html"
MD_RENDERED="${BUILD_DIR}/DISPOSITION_BUCKET_MODEL.rendered.md"

if [[ ! -f "$MD_SRC" ]]; then
  echo "error: missing $MD_SRC" >&2
  exit 1
fi

mkdir -p "$DIAG_DIR" "$BUILD_DIR"

echo "[bucket-model-pdf] extracting mermaid blocks..."
python3 - "$MD_SRC" "$DIAG_DIR" "$MD_RENDERED" <<'PY'
import re
import sys
from pathlib import Path

md_src = Path(sys.argv[1])
diag_dir = Path(sys.argv[2])
md_out = Path(sys.argv[3])
text = md_src.read_text(encoding="utf-8")
diag_dir.mkdir(parents=True, exist_ok=True)

pattern = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
idx = 0
parts = []
last = 0

for m in pattern.finditer(text):
    parts.append(text[last : m.start()])
    idx += 1
    name = f"diagram-{idx:02d}"
    mmd_path = diag_dir / f"{name}.mmd"
    svg_name = f"{name}.svg"
    mmd_path.write_text(m.group(1).strip() + "\n", encoding="utf-8")
    # Relative from docs/.build/bucket-model/ to docs/diagrams/bucket-model/
    img_rel = f"../../diagrams/bucket-model/{svg_name}"
    parts.append(f"![{name}]({img_rel})\n\n")
    last = m.end()

parts.append(text[last:])
md_out.parent.mkdir(parents=True, exist_ok=True)
md_out.write_text("".join(parts), encoding="utf-8")
print(f"  wrote {idx} mermaid files to {diag_dir}")
print(f"  rendered markdown: {md_out}")
PY

echo "[bucket-model-pdf] rendering mermaid → SVG..."
shopt -s nullglob
mmd_files=("$DIAG_DIR"/*.mmd)
render_kroki_svg() {
  local mmd="$1" svg="$2"
  curl -sS -f -X POST "https://kroki.io/mermaid/svg" --data-binary @"$mmd" -o "$svg"
}

if [[ ${#mmd_files[@]} -eq 0 ]]; then
  echo "  no mermaid files found"
else
  for mmd in "${mmd_files[@]}"; do
    svg="${mmd%.mmd}.svg"
    rendered=0
    if command -v mmdc >/dev/null 2>&1; then
      echo "  mmdc -i $mmd -o $svg"
      if mmdc -i "$mmd" -o "$svg" -b transparent 2>/dev/null; then
        rendered=1
      fi
    fi
    if [[ "$rendered" -eq 0 ]]; then
      echo "  kroki.io ← $mmd"
      render_kroki_svg "$mmd" "$svg"
    fi
  done
fi

render_html() {
  if command -v pandoc >/dev/null 2>&1; then
    pandoc "$MD_RENDERED" \
      --standalone \
      --metadata title="Disposition, buckets, and review" \
      --css="${ROOT}/docs/.build/bucket-model/pdf.css" \
      -f gfm \
      -t html5 \
      -o "$HTML_OUT"
    return 0
  fi
  return 1
}

cat > "${BUILD_DIR}/pdf.css" <<'CSS'
body {
  font-family: "Segoe UI", system-ui, sans-serif;
  font-size: 11pt;
  line-height: 1.45;
  max-width: 7.5in;
  margin: 0.75in auto;
  color: #1a1a1a;
}
h1 { font-size: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 0.2em; }
h2 { font-size: 14pt; margin-top: 1.4em; color: #2a4a6a; }
h3 { font-size: 12pt; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; }
th { background: #f0f4f8; }
code { font-size: 0.9em; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
CSS

echo "[bucket-model-pdf] building HTML..."
if ! render_html; then
  echo "  pandoc not found — using minimal Python markdown fallback"
  python3 - "$MD_RENDERED" "$HTML_OUT" "${BUILD_DIR}/pdf.css" <<'PY'
import html
import re
import sys
from pathlib import Path

md = Path(sys.argv[1]).read_text(encoding="utf-8")
out = Path(sys.argv[2])
css_path = Path(sys.argv[3])
body_lines = []
in_table = False
for line in md.splitlines():
    if line.startswith("# "):
        body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
    elif line.startswith("## "):
        body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
    elif line.startswith("### "):
        body_lines.append(f"<h3>{html.escape(line[4:])}</h3>")
    elif line.strip() == "---":
        body_lines.append("<hr>")
    elif line.startswith("!["):
        m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if m:
            body_lines.append(f'<img src="{html.escape(m.group(2), quote=True)}" alt="{html.escape(m.group(1))}">')
    elif "|" in line and line.strip().startswith("|"):
        if not in_table:
            body_lines.append("<table>")
            in_table = True
        if re.match(r"^\|\s*[-:]+", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        tag = "th" if in_table and body_lines[-1] == "<table>" else "td"
        if tag == "th":
            body_lines.append("<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells) + "</tr>")
        else:
            body_lines.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
    else:
        if in_table:
            body_lines.append("</table>")
            in_table = False
        if line.strip():
            body_lines.append(f"<p>{html.escape(line)}</p>")
        else:
            body_lines.append("")
if in_table:
    body_lines.append("</table>")
css = css_path.read_text(encoding="utf-8")
out.write_text(
    f"<!DOCTYPE html><html><head><meta charset=utf-8>"
    f"<title>Disposition, buckets, and review</title><style>{css}</style></head>"
    f"<body>{''.join(body_lines)}</body></html>",
    encoding="utf-8",
)
PY
fi

find_chromium() {
  for c in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$c" >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  local win_chrome="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
  local win_edge="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
  if [[ -f "$win_chrome" ]]; then
    echo "$win_chrome"
    return 0
  fi
  if [[ -f "$win_edge" ]]; then
    echo "$win_edge"
    return 0
  fi
  return 1
}

win_path_to_file_url() {
  local path="$1"
  if [[ "$path" == /mnt/* ]] || [[ "$path" == /home/* ]]; then
    local win
    win=$(wslpath -w "$path" 2>/dev/null || true)
    if [[ -n "$win" ]]; then
      python3 - "$win" <<'PY'
import sys
p = sys.argv[1].replace("\\", "/")
if len(p) >= 2 and p[1] == ":":
    print(f"file:///{p}")
else:
    print("file://" + p)
PY
      return 0
    fi
  fi
  python3 -c "from pathlib import Path; print(Path('$path').resolve().as_uri())"
}

echo "[bucket-model-pdf] building PDF..."
if CHROME=$(find_chromium); then
  WIN_HTML="$HTML_OUT"
  if [[ "$HTML_OUT" == /home/* ]]; then
    WIN_HTML=$(wslpath -w "$HTML_OUT" 2>/dev/null || echo "$HTML_OUT")
  fi
  WIN_PDF="$PDF_OUT"
  if [[ "$PDF_OUT" == /home/* ]]; then
    WIN_PDF=$(wslpath -w "$PDF_OUT" 2>/dev/null || echo "$PDF_OUT")
  fi
  FILE_URL=$(win_path_to_file_url "$HTML_OUT")
  echo "  using: $CHROME"
  if [[ "$CHROME" == *".exe" ]]; then
    "$CHROME" --headless --disable-gpu --no-sandbox \
      --print-to-pdf="$WIN_PDF" \
      --print-to-pdf-no-header \
      "$FILE_URL"
  else
    "$CHROME" --headless --disable-gpu --no-sandbox \
      --print-to-pdf="$PDF_OUT" \
      --print-to-pdf-no-header \
      "$FILE_URL" 2>/dev/null || \
    "$CHROME" --headless --disable-gpu \
      --print-to-pdf="$PDF_OUT" \
      "$FILE_URL"
  fi
  echo "[bucket-model-pdf] wrote $PDF_OUT"
elif command -v pandoc >/dev/null 2>&1 && command -v xelatex >/dev/null 2>&1; then
  pandoc "$MD_RENDERED" -o "$PDF_OUT" --pdf-engine=xelatex -V geometry:margin=1in
  echo "[bucket-model-pdf] wrote $PDF_OUT (pandoc/xelatex)"
elif command -v pandoc >/dev/null 2>&1 && command -v wkhtmltopdf >/dev/null 2>&1; then
  pandoc "$MD_RENDERED" -o "$PDF_OUT" --pdf-engine=wkhtmltopdf
  echo "[bucket-model-pdf] wrote $PDF_OUT (pandoc/wkhtmltopdf)"
else
  echo "error: need chromium or pandoc+PDF engine to produce PDF" >&2
  echo "  HTML available at: $HTML_OUT" >&2
  echo "  Install: sudo apt install chromium-browser   OR   sudo apt install pandoc texlive-xelatex" >&2
  exit 1
fi

ls -lh "$PDF_OUT"
