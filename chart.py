"""Elo trend chart, rendered to a PNG sized for a Discord embed.

Palette is anchored on the embed surface colour so the image reads as part of the
message rather than a pasted-in picture. The series colour was validated against
that surface: in-band lightness, above the chroma floor, and past 3:1 contrast.

Uses the Figure API rather than pyplot: pyplot keeps global state and is not safe
to drive from the worker threads these renders run on.
"""
import io

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.figure import Figure

SURFACE = "#242429"
SERIES = "#C98500"
GRID = "#2F2F35"
INK_MUTED = "#8E8E98"
INK_PRIMARY = "#FFFFFF"


def render_elo(values: list[int]) -> bytes:
    """Line chart of elo after each match, oldest to newest."""
    low, high = min(values), max(values)
    pad = max((high - low) * 0.12, 15)

    fig = Figure(figsize=(7.2, 2.7), dpi=150, facecolor=SURFACE)
    ax = fig.subplots()
    ax.set_facecolor(SURFACE)

    x = range(len(values))
    last = len(values) - 1
    bottom, top = low - pad, high + pad
    margin = max(last * 0.03, 0.4)

    ax.plot(x, values, color=SERIES, linewidth=2, solid_capstyle="round",
            solid_joinstyle="round", zorder=3)

    # Gradient wash under the line: a flat fill at any useful opacity reads as a
    # solid block and swamps the line itself.
    band = ax.fill_between(x, values, bottom, color="none", linewidth=0)
    red, green, blue = to_rgb(SERIES)
    fade = LinearSegmentedColormap.from_list(
        "fade", [(red, green, blue, 0.0), (red, green, blue, 0.32)])
    wash = ax.imshow(np.linspace(0, 1, 256).reshape(-1, 1), cmap=fade, origin="lower",
                     aspect="auto", extent=(-margin, last + margin, bottom, top), zorder=2)
    wash.set_clip_path(band.get_paths()[0], transform=ax.transData)

    # Only the latest point is marked. Its value is the embed's headline already,
    # so labelling it here would just repeat it on top of the line.
    ax.plot(last, values[-1], marker="o", markersize=6, color=SERIES,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)

    ax.set_xlim(-margin, last + margin)
    ax.set_ylim(bottom, top)
    ax.set_yticks([low, high])
    ax.tick_params(axis="y", colors=INK_MUTED, labelsize=10, length=0, pad=8)
    ax.tick_params(axis="x", length=0, labelbottom=False)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=1)

    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.tight_layout(pad=0.8)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    return buffer.getvalue()
