#!/usr/bin/env python3
"""
Generate the system architecture diagram for the thesis report.

Draws the Prometheus + cAdvisor + detector + dashboard data-flow pipeline as
a labelled box-and-arrow diagram using matplotlib (no graphviz binary needed).

Output:
    images/00_architecture_diagram.png   (high-DPI, suitable for thesis chapter 3)

Verification:
    - file exists, >= 30 KB, valid PNG signature
    - PIL can open it, dimensions >= 1600x1100

Usage:
    python -m pip install matplotlib pillow
    python generate_architecture_diagram.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
except ImportError:
    print("ERROR: matplotlib is not installed. Run: python -m pip install matplotlib pillow")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None


REPO_ROOT = Path(__file__).resolve().parent
IMAGES_DIR = REPO_ROOT / "images"
OUTPUT = IMAGES_DIR / "00_architecture_diagram.png"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MIN_BYTES = 30 * 1024
MIN_W = 1600
MIN_H = 1100


# ── Colours (ColorBrewer-style, print-friendly) ──────────────────────────────
COLOR_NODE = "#dbeafe"    # light blue — worker nodes
COLOR_PROM = "#fef3c7"    # light amber — Prometheus
COLOR_DET = "#dcfce7"     # light green — detector
COLOR_DASH = "#fce7f3"    # light pink — dashboard
COLOR_PAPER = "#ede9fe"   # light violet — Li et al. integration
COLOR_BORDER = "#1f2937"  # dark slate
COLOR_ARROW = "#374151"
COLOR_LABEL = "#111827"


def _box(ax, x, y, w, h, title, lines, fill, border=COLOR_BORDER):
    """Draw a rounded rectangle with a bold title and lines of body text."""
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        linewidth=1.4,
        edgecolor=border,
        facecolor=fill,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2, y + h - 0.18, title,
        ha="center", va="top",
        fontsize=12, fontweight="bold", color=COLOR_LABEL,
    )
    text_y = y + h - 0.45
    for line in lines:
        ax.text(
            x + 0.15, text_y, line,
            ha="left", va="top",
            fontsize=9, color=COLOR_LABEL,
            family="monospace",
        )
        text_y -= 0.22


def _arrow(ax, xy_from, xy_to, label=None, label_offset=(0, 0), curve=0.0):
    """Draw a labelled arrow between two points."""
    arrow = FancyArrowPatch(
        xy_from, xy_to,
        connectionstyle=f"arc3,rad={curve}",
        arrowstyle="-|>",
        mutation_scale=18,
        color=COLOR_ARROW,
        linewidth=1.6,
    )
    ax.add_patch(arrow)
    if label:
        mx = (xy_from[0] + xy_to[0]) / 2 + label_offset[0]
        my = (xy_from[1] + xy_to[1]) / 2 + label_offset[1]
        ax.text(
            mx, my, label,
            ha="center", va="center",
            fontsize=8.5, color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.9),
        )


def draw_diagram(out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(16, 11), dpi=160)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 11)
    ax.set_aspect("equal")
    ax.axis("off")

    # ── Title block ──────────────────────────────────────────────────────────
    ax.text(
        8, 10.55,
        "Zombie Container Detection — System Architecture",
        ha="center", va="center",
        fontsize=17, fontweight="bold", color=COLOR_LABEL,
    )
    ax.text(
        8, 10.18,
        "AWS EKS · 2 × t4g.small · Kubernetes 1.34 · single anchor paper: Li et al. (2025)",
        ha="center", va="center",
        fontsize=10, style="italic", color="#374151",
    )

    # ── Cluster boundary (dashed) ────────────────────────────────────────────
    cluster_box = FancyBboxPatch(
        (0.3, 0.3), 15.4, 9.4,
        boxstyle="round,pad=0.0,rounding_size=0.1",
        linewidth=1.5, edgecolor="#9ca3af", facecolor="none",
        linestyle=(0, (6, 4)),
    )
    ax.add_patch(cluster_box)
    ax.text(
        0.55, 9.55, "EKS cluster: zombie-detector-cluster (us-east-1)",
        fontsize=9.5, color="#6b7280", fontweight="bold",
    )

    # ── 12 test pods row ────────────────────────────────────────────────────
    _box(
        ax, x=0.7, y=7.6, w=4.6, h=1.7,
        title="test-scenarios namespace (12 pods)",
        lines=[
            "5 zombies   : low-cpu, mem-leak,",
            "              stuck-process, net-timeout,",
            "              resource-imbalance",
            "2 normals   : web, batch",
            "5 adversarial probes (FP/FN tests)",
        ],
        fill=COLOR_NODE,
    )

    # ── kubelet + cAdvisor ──────────────────────────────────────────────────
    _box(
        ax, x=5.6, y=7.6, w=4.6, h=1.7,
        title="Each EKS worker node",
        lines=[
            "kubelet  (Kubernetes 1.34)",
            "  └─ cAdvisor (built-in)",
            "       reads cgroup v2",
            "       /sys/fs/cgroup/...",
            "  exposes :10250/metrics/cadvisor",
        ],
        fill=COLOR_NODE,
    )

    # ── Prometheus ──────────────────────────────────────────────────────────
    _box(
        ax, x=10.5, y=7.6, w=4.8, h=1.7,
        title="Prometheus  (monitoring ns)",
        lines=[
            "v2.51.0   scrape interval 15 s",
            "TSDB on emptyDir, 7-day retention",
            "scrape_jobs:",
            "  • kubernetes-nodes-cadvisor",
            "  • kubernetes-pods",
            "  • zombie-detector",
        ],
        fill=COLOR_PROM,
    )

    # arrows test-pods --(produce metrics in cgroup)--> cAdvisor
    _arrow(ax, (5.3, 8.45), (5.6, 8.45))
    ax.text(5.45, 8.95, "produces\ncgroup-v2 stats",
            ha="center", va="center", fontsize=8,
            color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#cbd5e1"))
    # cAdvisor --scrape--> Prometheus
    _arrow(ax, (10.2, 8.45), (10.5, 8.45))
    ax.text(10.35, 8.95, "HTTPS scrape\n15 s, bearer token",
            ha="center", va="center", fontsize=8,
            color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#cbd5e1"))

    # ── Detector pod (centre, the heart of the system) ──────────────────────
    _box(
        ax, x=4.0, y=4.4, w=8.0, h=2.6,
        title="zombie-detector pod   (image: python:3.11-slim, src/ from ConfigMap)",
        lines=[
            "1. metrics_collector.py  — HTTP /api/v1/query_range  (lib: requests)",
            "                          rate(container_cpu_usage_seconds_total[5m])",
            "                          container_memory_usage_bytes",
            "                          rate(container_network_*_bytes_total[5m])",
            "2. heuristics.py         — 5-rule engine, weights 0.35/0.25/0.15/0.15/0.10",
            "                          composite = Σ(w·s) + 0.3·max(s)        ∈ [0,100]",
            "3. exporter.py           — prometheus_client Gauges on :8080/metrics",
            "                          zombie_container_score{ns,pod,container}",
            "                          zombie_container_rule_score{...,rule=...}",
            "                          zombie_energy_waste_watts{container}",
            "interval: 5 min   |   resources: 100 m CPU / 256 Mi memory   |   3 h uptime, no OOM",
        ],
        fill=COLOR_DET,
    )

    # Prometheus -> Detector  (read PromQL): straight down on the LEFT
    _arrow(ax, (11.0, 7.6), (10.5, 7.0))
    ax.text(9.0, 7.3, "PromQL query_range\n60-min lookback\n~240 samples / metric",
            ha="center", va="center", fontsize=8.2, color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#cbd5e1"))

    # Detector -> Prometheus  (export gauges): straight up on the RIGHT
    _arrow(ax, (12.0, 7.0), (12.5, 7.6))
    ax.text(14.0, 7.3, "exporter.py\nprometheus_client\nscraped back 30 s",
            ha="center", va="center", fontsize=8.2, color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#cbd5e1"))

    # ── Streamlit dashboard ─────────────────────────────────────────────────
    _box(
        ax, x=0.7, y=1.4, w=6.6, h=2.4,
        title="Streamlit dashboard   (5 tabs)",
        lines=[
            "1. Live Detection      — score bar + rule heatmap",
            "2. Threshold vs Heuristic  — 75 % / 80 % F1 vs naive",
            "3. Energy & Cost Impact   — Li et al. formula",
            "4. Experimental Design    — why 7 + 5 = 12 pods",
            "5. Failure Modes (Adversarial) — 3 FP + 1 FN trade-off",
            "queries Prometheus for  zombie_container_*  metrics",
        ],
        fill=COLOR_DASH,
    )

    _arrow(ax, (4.0, 4.4), (4.0, 3.85))
    ax.text(4.0, 4.12, "queries  zombie_container_*  metrics",
            ha="center", va="center", fontsize=8.2, color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#cbd5e1"))

    # ── Li et al. integration block ─────────────────────────────────────────
    _box(
        ax, x=8.6, y=1.4, w=6.7, h=2.4,
        title="Anchor paper integration: Li et al. (2025)",
        lines=[
            "Energy model (their formula, our code):",
            "    P_waste = (cpu·3.7 W + mem·0.375 W/GB) · PUE(1.2)",
            "Cost model (added by this work):",
            "    USD/mo = max(cpu_share, mem_share) · $0.0416 · 720",
            "Carbon (added):  P_waste · 0.233 kg CO2/kWh  (us-east-1)",
            "Output: zombie_energy_waste_watts, zombie_monthly_cost_waste_usd",
            "Per-zombie audit feeds into EAES scaling decisions.",
        ],
        fill=COLOR_PAPER,
    )

    _arrow(ax, (11.9, 4.4), (11.9, 3.85))
    ax.text(11.9, 4.12, "energy_impact.py  →  per-zombie cost",
            ha="center", va="center", fontsize=8.2, color=COLOR_LABEL, style="italic",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#cbd5e1"))

    # ── Legend / key facts strip ────────────────────────────────────────────
    facts = [
        "scrape interval = 15 s    →    ~240 samples / metric / 60-min window",
        "5 rules: low-cpu (35 %) | mem-leak (25 %) | stuck-process (15 %) | net-timeout (15 %) | resource-imbalance (10 %)",
        "Live measured: 75 % accuracy · 80 % F1 · 100 % recall · 50 % FPR  (12-container set)",
    ]
    for i, fact in enumerate(facts):
        ax.text(
            0.5, 0.85 - i * 0.22, "• " + fact,
            fontsize=9, color=COLOR_LABEL,
        )

    # ── Save ─────────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(out_path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def verify(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "file does not exist"
    size = path.stat().st_size
    if size < MIN_BYTES:
        return False, f"file too small ({size} bytes < {MIN_BYTES})"
    if path.read_bytes()[:8] != PNG_SIGNATURE:
        return False, "PNG signature missing"
    if Image is not None:
        with Image.open(path) as img:
            w, h = img.size
            if w < MIN_W or h < MIN_H:
                return False, f"dimensions too small ({w}x{h})"
            return True, f"{size // 1024} KB · {w}x{h} · format={img.format}"
    return True, f"{size // 1024} KB (Pillow not installed, dim check skipped)"


def main() -> int:
    print("Generating architecture diagram...")
    draw_diagram(OUTPUT)
    print(f"Wrote: {OUTPUT}")

    print()
    print("Verifying...")
    ok, detail = verify(OUTPUT)
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {OUTPUT.name}  -- {detail}")

    if ok:
        print()
        print("Open it to inspect visually:")
        print(f"  {OUTPUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
