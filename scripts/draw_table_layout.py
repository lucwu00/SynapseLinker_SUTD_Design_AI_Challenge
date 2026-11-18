"""Draw docs/table_layout_60cm.png from config/table_map_60cm.json (all dimensions in cm)."""
import json, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch

m = json.load(open("config/table_map_60cm.json"))
T = m["track"]; W = m["table"]["w"] * 100
lo, r, tw, ml = T["inset_m"] * 100, T["corner_radius_m"] * 100, T["tape_width_m"] * 100, T["mark_length_m"] * 100
hi = W - lo
INK, TAPE, CAST, MUTED, WARN, INFO = "#17232d", "#1b1f24", "#0e6f73", "#5b6b78", "#a35a00", "#2d6a9f"

fig, ax = plt.subplots(figsize=(9.2, 10.2), dpi=150)
ax.set_xlim(-9, W + 9); ax.set_ylim(-14, W + 6); ax.set_aspect("equal"); ax.axis("off")
ax.add_patch(Rectangle((0, 0), W, W, fc="#fbfcfd", ec="#9fb0bf", lw=1.6))
for g in range(5, int(W), 5):
    ax.plot([g, g], [0, W], color="#e6ecf1", lw=0.6, zorder=0); ax.plot([0, W], [g, g], color="#e6ecf1", lw=0.6, zorder=0)
for g in range(0, int(W) + 1, 10):
    ax.text(g, -1.6, f"{g}", ha="center", va="top", fontsize=7, color=MUTED)
    ax.text(-1.4, g, f"{g}", ha="right", va="center", fontsize=7, color=MUTED)

# tape loop (rounded square)
ax.add_patch(FancyBboxPatch((lo + r, lo + r), hi - lo - 2 * r, hi - lo - 2 * r, boxstyle=f"round,pad={r}",
                            fc="none", ec=TAPE, lw=tw * 3.05, zorder=2))
# marks
nodes = m["nodes"]
names = {"START": "START\nRegistration", "N_C": "Right station\nX-ray Room 2", "N_B": "Top station\nX-ray Room 1",
         "N_A": "Left station\nConsultation Room 1"}
for nid, lab in names.items():
    x, y = nodes[nid]["x"] * 100, nodes[nid]["y"] * 100
    horiz = abs(y - W / 2) > abs(x - W / 2)
    if horiz: ax.add_patch(Rectangle((x - tw / 2, y - ml / 2), tw, ml, fc=TAPE, zorder=3))
    else: ax.add_patch(Rectangle((x - ml / 2, y - tw / 2), ml, tw, fc=TAPE, zorder=3))
    ox = 0 if horiz else (1 if x > W / 2 else -1); oy = (1 if y > W / 2 else -1) if horiz else 0
    tx, ty = x + ox * 7.5, y + oy * (6.6 if oy > 0 else 8.6)
    ax.text(tx, ty, lab, ha="center", va="center", fontsize=8.2, color=INK, fontweight="bold" if nid == "START" else None,
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec=CAST if nid != "START" else INK, lw=1))
    cx_, cy_ = (x + 5.2, y + oy * 2.2) if horiz else (x - ox * 3.0, y + 2.6)
    ax.text(cx_, cy_, f"({x:.0f}, {y:.0f})", ha="center", va="center", fontsize=6.6, color=INFO, zorder=7,
            bbox=dict(fc="white", ec="none", pad=0.3, alpha=0.85))

# person-block zones
for rid, room in m["rooms"].items():
    zx, zy = room["zone"]["x"] * 100, room["zone"]["y"] * 100
    ax.add_patch(Rectangle((zx - 2, zy - 2), 4, 4, fc="#fff4e5", ec=WARN, lw=1.2, ls="--", zorder=3))
    ax.text(zx, zy, "P", ha="center", va="center", fontsize=7, color=WARN, fontweight="bold")
ax.text(30, 23.2, "P = 'person' block for an\noccupied room, 12 cm inside\nthe tape, level with its mark",
        ha="center", va="center", fontsize=6.8, color=WARN)

# robot at start, arrow counter-clockwise
ax.add_patch(FancyBboxPatch((30 - 5, 13 - 5.5), 10, 11, boxstyle="round,pad=0.3", fc="#d9ecec", ec=CAST, lw=1.0, alpha=0.45, zorder=4))
ax.add_patch(FancyArrowPatch((27.5, 13), (36.5, 13), arrowstyle="-|>", mutation_scale=14, color=CAST, lw=2, zorder=5))
ax.text(1.2, 9.5, "EDi-01 (shaded, about 10 cm)\nstarts with its wheels on\nthe START mark, facing right\n(counter-clockwise)", ha="left", va="center",
        fontsize=6.8, color=CAST)
# demo obstacle
ob = m["demo_obstacle"]; bx, by = ob["x"] * 100, ob["y"] * 100
ax.add_patch(Rectangle((bx - 2, by - 2), 4, 4, fc=WARN, ec="none", zorder=5, alpha=0.9))
ax.annotate("Scene 9 trolley box (41, 13)\nput down only for scene 9", (bx + 1.5, by - 2), (bx - 1, 3.2), fontsize=6.8, color=WARN,
            arrowprops=dict(arrowstyle="-", color=WARN, lw=0.8))

# dimensions
def dim(x0, y0, x1, y1, text, off=(0, 0), rot=0):
    ax.annotate("", (x0, y0), (x1, y1), arrowprops=dict(arrowstyle="<->", color=INFO, lw=0.8))
    ax.text((x0 + x1) / 2 + off[0], (y0 + y1) / 2 + off[1], text, ha="center", va="center", fontsize=7, color=INFO, rotation=rot,
            bbox=dict(fc="white", ec="none", pad=0.4))
dim(0, 58, lo, 58, "13", (0, 0))
dim(lo, 58, hi, 58, "34 cm, tape centre to tape centre", (0, 0))
dim(hi, 58, W, 58, "13", (0, 0))
dim(57, 0, 57, lo, "13", (0, 0), 90)
ax.text(lo + r + 1.2, hi - r - 1.2, f"corner\nradius {r:.0f}", fontsize=7, color=INFO, ha="center", va="center")
ax.text(W / 2, -6.5, "Presenter / audience side (bottom edge of the 60 x 60 cm table). Grid = 5 cm. Coordinates in cm from the bottom-left corner.",
        ha="center", va="center", fontsize=7.4, color=INK)
ax.text(W / 2, -10.2, "Tape: 19 mm black electrical tape on a white board. Marks: 6 cm bars across the line (adjust so 4-5 sensor LEDs light).",
        ha="center", va="center", fontsize=7.4, color=MUTED)
ax.text(W / 2, W + 3.2, "Synapse Linker demo table: tape loop and station positions", ha="center", va="center", fontsize=11, color=INK,
        fontweight="bold")
fig.savefig("docs/table_layout_60cm.png", bbox_inches="tight", facecolor="white")
print("ok")
