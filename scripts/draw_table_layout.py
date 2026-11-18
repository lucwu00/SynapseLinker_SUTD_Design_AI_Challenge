"""Draw docs/table_layout_60cm.png from config/table_map_60cm.json (all dimensions in cm).
Run from the project root:  python scripts/draw_table_layout.py"""
import json
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch, PathPatch

m = json.load(open("config/table_map_60cm.json"))
T = m["track"]; W = m["table"]["w"] * 100
r, tw, ml = T["corner_radius_m"] * 100, T["tape_width_m"] * 100, T["mark_length_m"] * 100
N = {k: (v["x"] * 100, v["y"] * 100) for k, v in m["nodes"].items()}
INK, TAPE, CAST, MUTED, WARN, INFO = "#17232d", "#1b1f24", "#0e6f73", "#5b6b78", "#a35a00", "#2d6a9f"

fig, ax = plt.subplots(figsize=(9.2, 10.4), dpi=150)
ax.set_xlim(-9, W + 9); ax.set_ylim(-17, W + 6); ax.set_aspect("equal"); ax.axis("off")
ax.add_patch(Rectangle((0, 0), W, W, fc="#fbfcfd", ec="#9fb0bf", lw=1.6))
for g in range(5, int(W), 5):
    ax.plot([g, g], [0, W], color="#e6ecf1", lw=0.6, zorder=0); ax.plot([0, W], [g, g], color="#e6ecf1", lw=0.6, zorder=0)
for g in range(0, int(W) + 1, 10):
    ax.text(g, -1.6, f"{g}", ha="center", va="top", fontsize=7, color=MUTED)
    ax.text(-1.4, g, f"{g}", ha="right", va="center", fontsize=7, color=MUTED)

lw_pts = tw * 3.05                    # tape width in points at this scale


def corridor(e):
    pts = [N[e["from"]]] + [(x * 100, y * 100) for x, y in e.get("via", [])] + [N[e["to"]]]
    verts, codes = [pts[0]], [Path.MOVETO]
    for i in range(1, len(pts) - 1):
        p, c, n = pts[i - 1], pts[i], pts[i + 1]
        d1, d2 = math.dist(p, c), math.dist(c, n)
        a = (c[0] + (p[0] - c[0]) * r / d1, c[1] + (p[1] - c[1]) * r / d1)
        b = (c[0] + (n[0] - c[0]) * r / d2, c[1] + (n[1] - c[1]) * r / d2)
        verts += [a, c, b]; codes += [Path.LINETO, Path.CURVE3, Path.CURVE3]
    verts.append(pts[-1]); codes.append(Path.LINETO)
    ax.add_patch(PathPatch(Path(verts, codes), fc="none", ec=TAPE, lw=lw_pts, capstyle="projecting", zorder=2))


for e in m["edges"]:
    corridor(e)


def tangent(nid):
    e = next(e for e in m["edges"] if nid in (e["from"], e["to"]))
    pts = [N[e["from"]]] + [(x * 100, y * 100) for x, y in e.get("via", [])] + [N[e["to"]]]
    p, q = (pts[0], pts[1]) if e["from"] == nid else (pts[-1], pts[-2])
    L = math.dist(p, q)
    return ((q[0] - p[0]) / L, (q[1] - p[1]) / L)


rooms_at = {v["node"]: v for v in m["rooms"].values()}
for nid, n in m["nodes"].items():
    x, y = N[nid]
    if n.get("kind") == "station":
        tx, ty = tangent(nid)
        horiz = abs(tx) > abs(ty)                       # tape runs left-right -> the bar crosses it up-down
        bw, bh = (tw, ml) if horiz else (ml, tw)
        ax.add_patch(Rectangle((x - bw / 2, y - bh / 2), bw, bh, fc=TAPE, zorder=3))
        room = rooms_at.get(nid)
        z = (room["zone"]["x"] * 100, room["zone"]["y"] * 100) if room else (x, y + 10)
        ox = 0 if abs(x - z[0]) < abs(y - z[1]) else (1 if x > z[0] else -1)
        oy = 0 if ox else (1 if y > z[1] else -1)
        lab = f"{room['classroom_label']}\n{room['label']}" if room else "START\nRegistration"
        lx, ly = (x + ox * 9.5, y + oy * 8.3) if oy <= 0 else (x - 13, y + 5.5)
        ax.text(lx, ly, lab, ha="center", va="center", fontsize=8.2, color=INK,
                fontweight="bold" if not room else None,
                bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=CAST if room else INK, lw=1))
        cx_, cy_ = (x - ox * 5.6, y + 2.6) if ox else (x + 5.6, y - oy * 2.4)
        ax.text(cx_, cy_, f"({x:.0f}, {y:.0f})", ha="center", va="center", fontsize=6.6, color=INFO, zorder=7,
                bbox=dict(fc="white", ec="none", pad=0.3, alpha=0.85))
    elif n.get("kind") == "junction":
        ax.text(x + (-4.8 if x > W / 2 else 4.8), y + 2.2, f"T-junction\n({x:.0f}, {y:.0f})", ha="center", va="center",
                fontsize=6.6, color=INFO, zorder=7, bbox=dict(fc="white", ec="none", pad=0.3, alpha=0.85))

for rid, room in m["rooms"].items():
    zx, zy = room["zone"]["x"] * 100, room["zone"]["y"] * 100
    ax.add_patch(Rectangle((zx - 2, zy - 2), 4, 4, fc="#fff4e5", ec=WARN, lw=1.2, ls="--", zorder=3))
    ax.text(zx, zy, "P", ha="center", va="center", fontsize=7, color=WARN, fontweight="bold")
ax.text(W / 2, -14.0, "P = 'person' block (about 4 x 4 x 8 cm) for an occupied room: 11 cm inside the tape, level with its station bar.",
        ha="center", va="center", fontsize=7.4, color=WARN)
ax.text(30, 33.2, "middle corridor (alternative route)", ha="center", va="center", fontsize=6.8, color=MUTED)

sx, sy = N[m["start_node"]]
ax.add_patch(FancyBboxPatch((sx - 5, sy - 5.5), 10, 11, boxstyle="round,pad=0.3", fc="#d9ecec", ec=CAST, lw=1.0, alpha=0.45, zorder=4))
ax.add_patch(FancyArrowPatch((sx - 2.5, sy), (sx + 6.5, sy), arrowstyle="-|>", mutation_scale=14, color=CAST, lw=2, zorder=5))
ax.text(1.2, 5.0, "EDi-01 starts with its wheels on\nthe START bar, facing right", ha="left", va="center", fontsize=6.8, color=CAST)
box = m["demo"]["scene9"]["box"]; bx, by = box["x"] * 100, box["y"] * 100
ax.add_patch(Rectangle((bx - 2, by - 2), 4, 4, fc=WARN, ec="none", zorder=5, alpha=0.9))
ax.annotate(f"Scene 9 trolley box ({bx:.0f}, {by:.0f})\nput down only for scene 9", (bx + 1.5, by - 2), (bx + 2, 3.0),
            fontsize=6.8, color=WARN, arrowprops=dict(arrowstyle="-", color=WARN, lw=0.8))

lo, hi = T["inset_m"] * 100, W - T["inset_m"] * 100


def dim(x0, y0, x1, y1, text, rot=0):
    ax.annotate("", (x0, y0), (x1, y1), arrowprops=dict(arrowstyle="<->", color=INFO, lw=0.8))
    ax.text((x0 + x1) / 2, (y0 + y1) / 2, text, ha="center", va="center", fontsize=7, color=INFO, rotation=rot,
            bbox=dict(fc="white", ec="none", pad=0.4))


dim(0, 58, lo, 58, f"{lo:.0f}")
dim(lo, 58, hi, 58, f"{hi - lo:.0f} cm, tape centre to tape centre")
dim(hi, 58, W, 58, f"{lo:.0f}")
dim(45, lo, 45, 30, "20", 90)
dim(22, 30, 22, hi, "20", 90)
ax.text(17, 14.5, f"corners:\nradius {r:.0f}", fontsize=6.6, color=INFO, ha="center", va="center")
ax.text(W / 2, -6.5, "Presenter / audience side (bottom edge of the 60 x 60 cm table). Grid = 5 cm. Coordinates in cm from the bottom-left corner.",
        ha="center", va="center", fontsize=7.4, color=INK)
ax.text(W / 2, -10.4, f"Tape: 19 mm black electrical tape on a white board. Station bars: {ml:.0f} cm across the line. "
        "Junctions: tape simply joins, no bar.", ha="center", va="center", fontsize=7.4, color=MUTED)
ax.text(W / 2, W + 3.2, "Synapse Linker demo table: double-loop track and station positions", ha="center", va="center",
        fontsize=11, color=INK, fontweight="bold")
fig.savefig("docs/table_layout_60cm.png", bbox_inches="tight", facecolor="white")
print("ok")
