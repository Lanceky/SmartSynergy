"""Render docs/architecture.svg.

Devpost needs an uploadable architecture diagram, and Mermaid does not render
there. This writes a self-contained SVG (no external fonts, no JS, no markers)
so the same diagram survives GitHub, Devpost, slide decks and the demo video.

Arrowheads are drawn as explicit polygons rather than SVG markers: several
renderers (ImageMagick among them) silently drop marker-end, which turns every
arrow into an ambiguous line.

    python scripts/make_architecture_diagram.py
"""

from __future__ import annotations

import math
import pathlib
from xml.sax.saxutils import escape

W, H = 1180, 1000

INK = "#10243e"
MUTED = "#5b7089"
LINE = "#8ea3bb"
BG = "#ffffff"

AGENT = ("#e8f1fb", "#2f6fb5")
GATE = ("#fdf0e3", "#c9781d")
DATA = ("#eef1f5", "#66788c")
ALERT = ("#fdecec", "#c0392b")
QUIET = ("#eaf5ec", "#3f7a4b")

FONT = "Inter, Segoe UI, Helvetica Neue, Arial, sans-serif"
MONO = "SFMono-Regular, Menlo, Consolas, monospace"

parts: list[str] = []


def add(s: str) -> None:
    parts.append(s)


def text(x, y, s, size=13, weight="normal", fill=INK, anchor="middle", italic=False, font=FONT):
    style = f"font-size:{size}px;font-weight:{weight};fill:{fill}"
    if italic:
        style += ";font-style:italic"
    add(
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" style="{style}" '
        f'font-family="{font}">{escape(s)}</text>'
    )


def box(x, y, w, h, lines, palette=DATA, rx=10, mono_first=False):
    fill, stroke = palette
    add(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
    )
    block = sum(ln[1] + 5 for ln in lines) - 5
    cy = y + h / 2 - block / 2
    for i, (s, size, *rest) in enumerate(lines):
        weight = rest[0] if rest else ("600" if i == 0 else "normal")
        fill_t = rest[1] if len(rest) > 1 else (INK if i == 0 else MUTED)
        italic = rest[2] if len(rest) > 2 else False
        cy += size
        text(
            x + w / 2,
            cy,
            s,
            size,
            weight,
            fill_t,
            italic=italic,
            font=MONO if (mono_first and i == 0) else FONT,
        )
        cy += 5


def _head(x, y, dx, dy, color):
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px, py = -uy, ux
    size, half = 9.0, 4.2
    bx, by = x - ux * size, y - uy * size
    pts = f"{x:.1f},{y:.1f} {bx + px * half:.1f},{by + py * half:.1f} {bx - px * half:.1f},{by - py * half:.1f}"
    add(f'<polygon points="{pts}" fill="{color}"/>')


def arrow(x1, y1, x2, y2, label=None, dashed=False, color=LINE, lx=None, ly=None, curve=None):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    if curve is None:
        d = f"M {x1} {y1} L {x2} {y2}"
        dx, dy = x2 - x1, y2 - y1
    else:
        cx, cy = curve
        d = f"M {x1} {y1} Q {cx} {cy} {x2} {y2}"
        dx, dy = x2 - cx, y2 - cy
    n = math.hypot(dx, dy) or 1.0
    # Stop the stroke short so it does not poke through the arrowhead tip.
    ex, ey = x2 - dx / n * 7.5, y2 - dy / n * 7.5
    d = d.rsplit(" ", 2)[0] + f" {ex:.1f} {ey:.1f}"
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7"{dash}/>')
    _head(x2, y2, dx, dy, color)
    if label:
        text(
            lx if lx is not None else (x1 + x2) / 2,
            ly if ly is not None else (y1 + y2) / 2 - 7,
            label,
            9.5,
            "700",
            MUTED,
        )


def container(x, y, w, h, title, note=None):
    add(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
        f'stroke="{LINE}" stroke-width="1.4" stroke-dasharray="7 5"/>'
    )
    text(x + 16, y + 23, title, 12.5, "700", MUTED, anchor="start")
    if note:
        text(x + w - 16, y + 23, note, 11, "normal", MUTED, anchor="end", italic=True)


add(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
    f'viewBox="0 0 {W} {H}" role="img" aria-label="SmartSynergy architecture">'
)
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

text(40, 46, "SmartSynergy", 25, "700", INK, anchor="start")
text(
    40,
    70,
    "A background evidence watch for combination-therapy hypotheses. "
    "It stays silent unless the evidence actually moves.",
    13,
    "normal",
    MUTED,
    anchor="start",
)

box(40, 100, 250, 62, [("cron / EventBridge", 13), ("worker run-due", 11.5, "normal", MUTED)], DATA)
box(
    330,
    100,
    410,
    62,
    [("Run controller", 13), ("run lock · daily cap · incremental window + 7-day overlap", 11)],
    DATA,
)
arrow(292, 131, 328, 131)

GX, GY, GW, GH = 40, 196, 700, 390
container(GX, GY, GW, GH, "STRANDS GRAPH", "one hypothesis per invocation")

AX, AW = 130, 250
box(
    AX,
    242,
    AW,
    78,
    [
        ("Analyst", 14),
        ("Strands Agent · structured_output", 11),
        ("extracts claims + verbatim quotes", 11),
    ],
    AGENT,
)
box(
    AX,
    348,
    AW,
    78,
    [
        ("Critic", 14),
        ("Strands Agent · adversarial", 11),
        ("attacks every claim it is given", 11),
    ],
    AGENT,
)
box(
    AX,
    454,
    AW,
    86,
    [
        ("Evidence gate", 14),
        ("custom MultiAgentBase node", 11),
        ("deterministic Python, no model", 11, "normal", GATE[1], True),
    ],
    GATE,
)
box(
    450,
    454,
    230,
    86,
    [
        ("Editor", 14),
        ("Strands Agent", 11),
        ("structured_output", 11),
        ("writes the decision brief", 11),
    ],
    AGENT,
)

arrow(AX + AW / 2, 320, AX + AW / 2, 346)
arrow(AX + AW / 2, 426, AX + AW / 2, 452)
arrow(AX + AW, 497, 448, 497, "GATE_PASSED", lx=415, ly=487)

arrow(AX, 497, AX, 283, dashed=True, color=GATE[1], curve=(56, 390))
# translate-then-rotate is the portable form; `transform` combined with x/y
# attributes is mis-placed by several rasterisers.
add(
    f'<g transform="translate(78,390) rotate(-90)">'
    f'<text x="0" y="0" text-anchor="middle" '
    f'style="font-size:10.5px;font-weight:700;fill:{GATE[1]}" font-family="{FONT}">'
    "FOLLOWUP_REQUIRED · once only</text></g>"
)

TX = 450
container(TX, 236, 270, 196, "TOOLS", "budget-bounded")
for i, (name, sub) in enumerate(
    [
        ("search_literature", "Europe PMC · open access"),
        ("get_source_text", "cached on disk, hashed"),
        ("resolve_compound", "PubChem · name → CID"),
    ]
):
    box(TX + 15, 262 + i * 56, 240, 46, [(name, 12.5), (sub, 10.5)], DATA, mono_first=True)
arrow(AX + AW, 281, TX - 4, 300, dashed=True)
arrow(AX + AW, 387, TX - 4, 368, dashed=True)

RX = 770
box(
    RX,
    242,
    400,
    82,
    [
        ("Final citation gate", 13.5),
        ("the brief is rejected if it cites a claim ID that", 11),
        ("does not exist. Hallucinated evidence cannot", 11),
        ("reach a human.", 11),
    ],
    GATE,
)
box(
    RX,
    350,
    400,
    68,
    [
        ("Fingerprint diff", 13.5),
        ("order- and prose-independent: rewording", 11),
        ("the same evidence is not a change", 11),
    ],
    DATA,
)
arrow(680, 497, RX - 4, 300, curve=(735, 420))
arrow(RX + 200, 324, RX + 200, 348)

box(
    RX,
    454,
    185,
    74,
    [("silence", 13.5, "700", QUIET[1]), ("the common case,", 11), ("and the point", 11)],
    QUIET,
)
box(
    RX + 215,
    454,
    185,
    74,
    [("Decision inbox", 13.5, "700", ALERT[1]), ("only a material", 11), ("change interrupts", 11)],
    ALERT,
)
arrow(RX + 160, 418, RX + 100, 452, "unchanged", lx=RX + 78, ly=444)
arrow(RX + 240, 418, RX + 300, 452, "material change", lx=RX + 330, ly=444)

box(
    40,
    626,
    560,
    84,
    [
        ("SQLite ledger   (append-only, idempotent)", 13.5),
        ("sources · claims · dossier versions · runs · review items · watermarks", 11),
        ("every claim keeps its source, its quote and its verification status", 11),
    ],
    DATA,
)
box(
    770,
    626,
    400,
    84,
    [
        ("Streamlit console", 13.5),
        ("decision inbox · evidence receipts", 11),
        ("dossier history · full run audit trail", 11),
    ],
    DATA,
)
arrow(RX + 92, 528, 565, 624, curve=(730, 632))
arrow(RX + 307, 528, RX + 307, 624)
arrow(602, 668, 766, 668)

container(40, 744, 1130, 212, "WHERE TRUST LIVES", "deterministic Python, not prompt instructions")
rules = [
    (
        "Unverifiable quote",
        "every quoted passage must appear verbatim in the stored",
        "text, or the claim is flagged unverified",
    ),
    (
        "Invented source",
        "a claim citing a source ID the agent never retrieved",
        "is deleted before a human can read it",
    ),
    (
        "Overstated relevance",
        "a critic finding of context mismatch forcibly demotes",
        "a claim from direct to indirect",
    ),
    (
        "Retracted or corrected",
        "a correction flag demotes support to unclear,",
        "regardless of what the model concluded",
    ),
]
for i, (title, l1, l2) in enumerate(rules):
    x = 64 + (i % 2) * 546
    y = 776 + (i // 2) * 88
    box(x, y, 522, 74, [(title, 13), (l1, 11), (l2, 11)], GATE)

text(
    W / 2,
    H - 16,
    "No synergy score. The evidence dimensions stay separate, because averaging a cell-line "
    "result with a phase III trial destroys the only thing that matters.",
    11.5,
    "normal",
    MUTED,
    italic=True,
)

add("</svg>")

out = pathlib.Path(__file__).resolve().parent.parent / "docs" / "architecture.svg"
out.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size:,} bytes)")
