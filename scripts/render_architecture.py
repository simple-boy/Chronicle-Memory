"""Render the implementation-matched Chronicle-Memory V2 method figure.

Figure contract
---------------
Core conclusion: V2 preserves ordered source evidence and its provenance while
using temporal, entity, relational, and lexical indexes to retrieve bounded,
ranked evidence for the external AML Answer/Eval pipeline.
Archetype: two-lane method schematic (write above, read below).
Backend: Python/matplotlib. No quantitative results are shown.
Export: editable SVG and PDF plus a high-resolution PNG preview.

The diagram deliberately excludes unimplemented embeddings, LLM extraction,
lossy consolidation, materialized episodic/profile memory, and participant-side
answer generation. It is not a performance claim.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "chronicle-memory-v2-framework"

mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "font.size": 14,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "savefig.facecolor": "white",
    }
)

INK = "#18344C"
MUTED = "#566B7D"
BORDER = "#9BABBA"
BLUE = "#2B6C9B"
BLUE_PALE = "#EAF3FA"
TEAL = "#1D7D84"
TEAL_PALE = "#E9F5F3"
GREEN = "#398262"
GREEN_PALE = "#EAF5EC"
AMBER = "#C07B29"
AMBER_PALE = "#FFF4E4"
RED = "#C95C59"
RED_PALE = "#FFF0EE"
PURPLE = "#735C98"
PURPLE_PALE = "#F3EFF8"
WHITE = "#FFFFFF"
LIGHT = "#F7F9FB"

fig, ax = plt.subplots(figsize=(20, 11.35), dpi=300)
ax.set_xlim(0, 2000)
ax.set_ylim(1135, 0)
ax.axis("off")
fig.patch.set_facecolor(WHITE)
ax.set_facecolor(WHITE)
fig.subplots_adjust(left=0, right=1, top=1, bottom=0)


def label(x, y, value, *, size=15, color=INK, weight="normal", ha="left", va="center", **kwargs):
    return ax.text(
        x,
        y,
        value,
        fontsize=size,
        color=color,
        fontweight=weight,
        ha=ha,
        va=va,
        linespacing=1.18,
        zorder=8,
        **kwargs,
    )


def rounded(x, y, w, h, *, face=WHITE, edge=BORDER, lw=1.35, radius=14, linestyle="solid", z=2):
    p = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw,
        edgecolor=edge,
        facecolor=face,
        linestyle=linestyle,
        zorder=z,
    )
    ax.add_patch(p)
    return p


def region(x, y, w, h, title, *, color=INK, face=WHITE, dashed=True, title_size=20):
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            linewidth=1.7,
            edgecolor=BORDER,
            facecolor=face,
            linestyle=(0, (5, 4)) if dashed else "solid",
            zorder=0,
        )
    )
    label(x + 26, y + 27, title, size=title_size, color=color, weight="bold")


def path_arrow(points, *, color=BLUE, lw=2.25, dashed=False, head=16, z=6):
    style = (0, (5, 4)) if dashed else "solid"
    if len(points) > 2:
        xx = [point[0] for point in points[:-1]]
        yy = [point[1] for point in points[:-1]]
        ax.plot(xx, yy, color=color, linewidth=lw, linestyle=style, zorder=z)
    ax.add_patch(
        FancyArrowPatch(
            points[-2],
            points[-1],
            arrowstyle="-|>",
            mutation_scale=head,
            linewidth=lw,
            color=color,
            linestyle=style,
            shrinkA=0,
            shrinkB=0,
            zorder=z,
        )
    )


def circle_icon(x, y, text, *, face, edge, size=15):
    ax.add_patch(Ellipse((x, y), 35, 35, facecolor=face, edgecolor=edge, linewidth=1.4, zorder=5))
    label(x, y + 0.5, text, size=size, color=edge, weight="bold", ha="center")


def cylinder(x, y, w, h, *, face=GREEN_PALE, edge=GREEN, top=20, lw=1.5):
    ax.add_patch(Rectangle((x, y + top / 2), w, h - top, facecolor=face, edgecolor="none", zorder=3))
    ax.add_patch(Ellipse((x + w / 2, y + h - top / 2), w, top, facecolor=face, edgecolor=edge, linewidth=lw, zorder=3))
    ax.plot([x, x], [y + top / 2, y + h - top / 2], color=edge, linewidth=lw, zorder=4)
    ax.plot([x + w, x + w], [y + top / 2, y + h - top / 2], color=edge, linewidth=lw, zorder=4)
    ax.add_patch(Ellipse((x + w / 2, y + top / 2), w, top, facecolor="#F8FCF9", edgecolor=edge, linewidth=lw, zorder=4))


def small_index(y, title, subtitle):
    rounded(1622, y, 280, 53, face="#F8FCF9", edge=GREEN, lw=1.25, radius=10)
    label(1762, y + 19, title, size=14.5, weight="bold", color=INK, ha="center")
    label(1762, y + 41, subtitle, size=13.8, color=MUTED, ha="center")


# Header and the two method lanes.
label(40, 49, "Chronicle-Memory V2", size=27, weight="bold")
label(
    40,
    89,
    "Source-Preserving Temporal Evidence Retrieval for Long-Term Agent Memory",
    size=19,
    color=MUTED,
)
rounded(1665, 43, 290, 50, face=BLUE_PALE, edge=BLUE, lw=1.3, radius=13)
label(1810, 68, "CYCLE 2  ·  TEXTUAL", size=14.6, color=BLUE, weight="bold", ha="center")

region(25, 135, 1950, 410, "A   Incremental source memory writing", color=BLUE)
region(25, 610, 1950, 420, "B   Query-guided evidence retrieval", color=TEAL)

# A. Input stream.
rounded(45, 205, 250, 310, face=LIGHT, edge="#B9C7D3", radius=18)
circle_icon(78, 238, "1", face=WHITE, edge=BLUE)
label(105, 238, "Conversation", size=17, weight="bold")
rounded(66, 270, 208, 62, face=WHITE, edge="#9BB9CF", radius=11)
label(80, 291, "user  ·  message 1", size=14.2, color=BLUE, weight="bold")
label(80, 315, '"I like coffee."', size=14.3)
rounded(66, 346, 208, 72, face=WHITE, edge="#9BB9CF", radius=11)
label(80, 366, "user  ·  message 2", size=14.2, color=BLUE, weight="bold")
label(80, 392, '"from coffee to tea"', size=14.3)
label(66, 454, "role · content · timestamp?", size=14.1, color=MUTED)
label(66, 484, "user · session · request IDs", size=13.8, color=MUTED)

# A. Add/ingestion.
rounded(325, 205, 275, 310, face=BLUE_PALE, edge="#8FB3D1", radius=18)
circle_icon(357, 238, "2", face=WHITE, edge=BLUE)
label(385, 238, "Add + ingest", size=17, weight="bold")
rounded(347, 267, 231, 84, face=WHITE, edge=BLUE, radius=12)
label(362, 288, "POST /add", size=15.5, color=BLUE, weight="bold")
label(362, 315, "validate messages[]", size=14.2)
label(362, 337, "scope by user_id", size=14.2)
path_arrow([(463, 351), (463, 365)], color=BLUE, lw=1.8, head=12)
rounded(347, 365, 231, 91, face=WHITE, edge=BLUE, radius=12)
label(362, 388, "Atomic · idempotent", size=14.9, weight="bold")
label(362, 414, "one record / message", size=14.0)
label(362, 438, "stable evidence ID", size=14.0)
label(347, 483, "200 after durable commit", size=14.0, color=BLUE, weight="bold")

# A. Deterministic extraction.
rounded(630, 205, 285, 310, face=BLUE_PALE, edge="#8FB3D1", radius=18)
circle_icon(662, 238, "3", face=WHITE, edge=BLUE)
label(690, 238, "Parse cues", size=17, weight="bold")
rounded(650, 270, 245, 88, face=WHITE, edge=BLUE, radius=12)
label(666, 292, "Temporal cues", size=15.1, weight="bold")
label(666, 319, "source / event time", size=14.0)
label(666, 343, "relative if anchored", size=14.0)
rounded(650, 371, 245, 88, face=WHITE, edge=BLUE, radius=12)
label(666, 392, "Entity / relation", size=14.9, weight="bold")
label(666, 420, "rule-based extraction", size=14.0)
label(666, 444, "indexed links", size=14.0)
label(650, 484, "Source text unchanged", size=14.0, color=BLUE)

# A. Conservative state handling. Deletion and supersession have distinct paths.
rounded(945, 205, 275, 310, face=AMBER_PALE, edge="#DDB680", radius=18)
circle_icon(977, 238, "4", face=WHITE, edge=AMBER)
label(1005, 238, "State update", size=17, weight="bold")
rounded(965, 268, 235, 103, face=WHITE, edge=AMBER, radius=12)
label(981, 291, "Explicit from X to Y", size=14.9, weight="bold")
label(981, 320, "old fact → superseded", size=14.0)
label(981, 343, "history retained", size=14.0)
rounded(965, 389, 235, 94, face=RED_PALE, edge=RED, radius=12)
label(981, 413, "Direct user forget", size=14.9, color=RED, weight="bold")
label(981, 441, "exact full-fact match", size=14.0)
label(981, 465, "delete record + indexes", size=14.0)
path_arrow([(578, 339), (608, 339), (608, 526), (936, 526), (936, 436), (965, 436)], color=RED, lw=1.7, dashed=True, head=13)

# A. Persistent store and derived indexes.
rounded(1250, 190, 695, 325, face=GREEN_PALE, edge="#83BA9A", radius=18)
circle_icon(1283, 220, "5", face=WHITE, edge=GREEN)
label(1311, 220, "SQLite store + indexes", size=17, weight="bold")
cylinder(1280, 257, 309, 210, face="#F4FBF5", edge=GREEN, top=29, lw=1.6)
label(1435, 310, "Raw records", size=16.0, weight="bold", ha="center")
label(1435, 341, "content · role", size=14.0, ha="center")
label(1435, 369, "user · session · order", size=14.0, ha="center")
label(1435, 397, "time? · ingested_at · ID", size=14.0, ha="center")
label(1435, 421, "active  |  superseded", size=14.1, color=AMBER, weight="bold", ha="center")
small_index(247, "FTS5 lexical", "terms · BM25")
small_index(309, "Temporal B-tree", "source · event time")
small_index(371, "Entity / relation", "indexed tables")
small_index(433, "Add request ledger", "idempotent retries")
ax.plot([1604, 1604], [278, 459], color=GREEN, lw=1.7, zorder=5)
path_arrow([(1589, 351), (1604, 351)], color=GREEN, lw=1.7, head=11)
for index_y in (274, 336, 398, 460):
    path_arrow([(1604, index_y), (1621, index_y)], color=GREEN, lw=1.4, head=10)
label(1281, 493, "WAL · commit before 200", size=14.0, color=GREEN)

# Main write flow. State changes are represented in the store; direct deletion is red.
for left, right in ((295, 325), (600, 630), (915, 945), (1220, 1250)):
    path_arrow([(left, 319), (right, 319)], color=BLUE, lw=2.25)
path_arrow([(1200, 365), (1250, 365)], color=AMBER, lw=1.6, dashed=True, head=12)
path_arrow([(1200, 442), (1233, 442), (1233, 468), (1280, 468)], color=RED, lw=1.7, dashed=True, head=12)

# The store links writing and reading through a narrow shared-data channel.
path_arrow([(1578, 515), (1578, 575), (904, 575), (904, 675)], color=GREEN, lw=2.5, head=17)
rounded(1182, 557, 300, 34, face=WHITE, edge="#9FC3AA", lw=1.1, radius=8, z=6)
label(1332, 574, "read source + indexes", size=13.8, color=GREEN, weight="bold", ha="center")

# B. Query and query understanding.
rounded(45, 685, 250, 305, face=LIGHT, edge="#B9C7D3", radius=18)
circle_icon(78, 718, "1", face=WHITE, edge=TEAL)
label(105, 718, "Question", size=17, weight="bold")
rounded(65, 755, 210, 105, face=WHITE, edge="#9BB9CF", radius=12)
label(81, 778, "POST /search", size=15.2, color=TEAL, weight="bold")
label(81, 808, '"What do I prefer', size=14.4)
label(81, 833, 'now?"', size=14.4)
label(65, 900, "query · options? · top_k", size=14.0, color=MUTED)
label(65, 934, "exact user_id scope", size=14.0, color=MUTED)

rounded(325, 685, 270, 305, face=TEAL_PALE, edge="#92BFC0", radius=18)
circle_icon(357, 718, "2", face=WHITE, edge=TEAL)
label(385, 718, "Query cues", size=17, weight="bold")
rounded(348, 762, 224, 67, face=WHITE, edge=TEAL, radius=12)
label(362, 783, "Terms + aliases", size=14.8, weight="bold")
label(362, 808, "query expansion", size=14.0)
rounded(348, 843, 224, 76, face=WHITE, edge=TEAL, radius=12)
label(362, 865, "Entity · time · order", size=14.8, weight="bold")
label(362, 893, "query-only intent cues", size=14.0)
label(348, 953, "No future context", size=14.0, color=TEAL)

# B. Parallel candidate lanes.
rounded(620, 675, 575, 320, face="#FFF8ED", edge="#E0C89F", radius=18)
circle_icon(652, 708, "3", face=WHITE, edge=AMBER)
label(680, 708, "Parallel candidate union", size=17, weight="bold")


def candidate(x, y, title, detail, tag):
    rounded(x, y, 260, 80, face=WHITE, edge="#D8B879", radius=11)
    rounded(x + 10, y + 17, 37, 36, face=AMBER_PALE, edge=AMBER, lw=1.1, radius=8)
    label(x + 28.5, y + 35, tag, size=14.2, weight="bold", color=AMBER, ha="center")
    label(x + 57, y + 28, title, size=14.9, weight="bold")
    label(x + 57, y + 56, detail, size=14.0, color=MUTED)


candidate(638, 743, "FTS5 / BM25", "query + options", "L")
candidate(914, 743, "Entity / relation", "indexed links · ≤2 hops", "E")
candidate(638, 834, "Source time / order", "dates · before / after", "T")
candidate(914, 834, "Context neighbors", "same / cross session", "N")
rounded(638, 926, 536, 52, face=WHITE, edge="#D8B879", radius=11)
label(658, 952, "Recent records  +  bounded fallback", size=14.1, weight="bold")

# B. Ranking by supported signals; no claim of calibrated probabilities.
rounded(1225, 685, 225, 305, face=AMBER_PALE, edge="#DDB680", radius=18)
circle_icon(1257, 718, "4", face=WHITE, edge=AMBER)
label(1285, 718, "Reranking", size=16.5, weight="bold")
label(1245, 761, "Weighted score", size=14.2, color=MUTED)
for yy, tx in (
    (801, "Lexical / BM25"),
    (841, "Entity / relation"),
    (881, "Temporal fit"),
    (921, "Status + recency"),
):
    ax.add_patch(Ellipse((1253, yy), 10, 10, facecolor=AMBER, edgecolor="none", zorder=5))
    label(1272, yy, tx, size=14.4)
label(1245, 966, "Strict global Top K", size=14.1, color=AMBER, weight="bold")

# B. What the participant returns: original source evidence plus provenance.
rounded(1480, 685, 240, 305, face=TEAL_PALE, edge="#92BFC0", radius=18)
circle_icon(1512, 718, "5", face=WHITE, edge=TEAL)
label(1540, 718, "Top-K evidence", size=16.7, weight="bold")
rounded(1500, 756, 200, 91, face=WHITE, edge=TEAL, radius=10)
label(1514, 776, "rank 1  ·  stable id", size=14.3, color=TEAL, weight="bold")
label(1514, 802, "original source text", size=14.0)
label(1514, 827, "role · session · order", size=13.9, color=MUTED)
rounded(1500, 860, 200, 91, face=WHITE, edge=TEAL, radius=10)
label(1514, 880, "rank 2  ·  stable id", size=14.3, color=TEAL, weight="bold")
label(1514, 906, "source_time?", size=14.0)
label(1514, 931, "ingested_at", size=13.9, color=MUTED)
label(1500, 971, "data[{id, content}]", size=14.1, color=TEAL, weight="bold")

# AML owns answer generation and evaluation, outside the participant service.
rounded(1750, 675, 190, 320, face=PURPLE_PALE, edge=PURPLE, lw=1.7, radius=18, linestyle=(0, (5, 4)))
label(1845, 713, "EXTERNAL AML", size=15.2, color=PURPLE, weight="bold", ha="center")
rounded(1770, 765, 150, 75, face=WHITE, edge=PURPLE, radius=12)
label(1845, 802, "Answer model", size=15.0, ha="center", weight="bold")
path_arrow([(1845, 840), (1845, 871)], color=PURPLE, lw=2.0, head=14)
rounded(1770, 871, 150, 75, face=WHITE, edge=PURPLE, radius=12)
label(1845, 908, "Evaluation", size=15.0, ha="center", weight="bold")
label(1845, 972, "platform-owned", size=14.0, color=PURPLE, ha="center")

# Main read path and external handoff.
for left, right in ((295, 325), (595, 620), (1195, 1225), (1450, 1480), (1720, 1750)):
    path_arrow([(left, 831), (right, 831)], color=TEAL, lw=2.25)

# Compact legend: path semantics are also distinguished by line style.
path_arrow([(70, 1070), (125, 1070)], color=TEAL, lw=2.3, head=13)
label(137, 1070, "read / write data path", size=14.0, color=MUTED)
path_arrow([(510, 1070), (565, 1070)], color=RED, lw=1.8, dashed=True, head=13)
label(578, 1070, "exact-fact deletion", size=14.0, color=MUTED)
path_arrow([(945, 1070), (1000, 1070)], color=AMBER, lw=1.8, dashed=True, head=13)
label(1013, 1070, "explicit status update", size=14.0, color=MUTED)
rounded(1490, 1051, 36, 36, face=PURPLE_PALE, edge=PURPLE, lw=1.4, radius=6, linestyle=(0, (4, 3)))
label(1540, 1070, "outside participant service", size=14.0, color=MUTED)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for suffix, options in (
        (".svg", {}),
        (".pdf", {}),
        (".png", {"dpi": 300}),
    ):
        fig.savefig(OUT.with_suffix(suffix), bbox_inches="tight", pad_inches=0.10, **options)
    plt.close(fig)
    print(f"Rendered {OUT}.svg, .pdf, and .png")


if __name__ == "__main__":
    main()
