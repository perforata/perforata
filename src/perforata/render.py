"""Matplotlib rendering helpers for previews (used by the Streamlit UI)."""

from __future__ import annotations


def plot_shapes(shapes: list[dict], ax=None, linewidth: float = 0.8,
                color: str = "black", title: str = ""):
    """Draw a shape list onto a matplotlib axis; returns the figure."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 8))
    else:
        fig = ax.figure

    xs, ys = [], []
    for s in shapes:
        if s["type"] == "circle":
            cx, cy = s["center"]
            r = s["radius"]
            ax.add_patch(Circle((cx, cy), r, fill=False,
                                edgecolor=color, linewidth=linewidth))
            xs.extend([cx - r, cx + r])
            ys.extend([cy - r, cy + r])
        else:
            ax.add_patch(Polygon(s["points"], closed=True, fill=False,
                                 edgecolor=color, linewidth=linewidth))
            xs.extend(p[0] for p in s["points"])
            ys.extend(p[1] for p in s["points"])

    if xs and ys:
        pad = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)

    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    if title:
        ax.set_title(title)
    return fig


def plot_pointcloud(cloud, ax=None, title: str = ""):
    """Scatter plot of a PointCloud, colored by tag (debug aid)."""
    import matplotlib.pyplot as plt

    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 8))
    else:
        fig = ax.figure

    tags = cloud.get("tag", "default")
    for tag in sorted(set(tags)):
        mask = tags == tag
        ax.scatter(cloud.x[mask], cloud.y[mask], s=8, label=str(tag))
    ax.set_aspect("equal")
    ax.legend(loc="upper right", fontsize=8)
    if title:
        ax.set_title(title)
    return fig
