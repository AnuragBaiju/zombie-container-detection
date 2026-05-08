"""
Prometheus metrics exporter for zombie detection results.

Exposes the heuristic's per-container and per-rule scores as Prometheus
gauges so any dashboard can visualise them. Energy / cost impact metrics
use the Li et al. (2025) model from src/energy_impact.py.
"""

import logging
from prometheus_client import Gauge, start_http_server

logger = logging.getLogger(__name__)

# ── Heuristic detection metrics ──────────────────────────────────────────────
ZOMBIE_SCORE = Gauge(
    "zombie_container_score",
    "Composite heuristic zombie score (0-100). >=60 = zombie, 30-60 = potential",
    ["namespace", "pod", "container"],
)

ZOMBIE_RULE_SCORE = Gauge(
    "zombie_container_rule_score",
    "Individual heuristic rule score (0-1)",
    ["namespace", "pod", "container", "rule"],
)

ZOMBIE_CLASSIFICATION = Gauge(
    "zombie_container_is_zombie",
    "1 if container classified as zombie, 0 otherwise",
    ["namespace", "pod", "container"],
)

ZOMBIE_POTENTIAL = Gauge(
    "zombie_container_is_potential",
    "1 if container classified as potential zombie, 0 otherwise",
    ["namespace", "pod", "container"],
)

DETECTION_TOTAL = Gauge("zombie_detection_total_containers",
                        "Total containers analysed in last run")
DETECTION_ZOMBIES = Gauge("zombie_detection_zombies_count",
                          "Zombies detected in last run")
DETECTION_POTENTIAL = Gauge("zombie_detection_potential_count",
                            "Potential zombies detected in last run")
DETECTION_NORMAL = Gauge("zombie_detection_normal_count",
                         "Normal containers in last run")

# -- Energy waste metrics (Li et al. 2025 model) --
ENERGY_WASTE_WATTS = Gauge(
    "zombie_energy_waste_watts",
    "Estimated power wasted by zombie container in watts (Li et al. 2025 model: cpu*3.7W + mem*0.375W/GB * PUE(1.2))",
    ["container"],
)

MONTHLY_COST_WASTE_USD = Gauge(
    "zombie_monthly_cost_waste_usd",
    "Estimated monthly AWS cost wasted by zombie container (USD). Proportional share of t3.medium.",
    ["container"],
)


def start_metrics_server(port: int = 8080):
    """Start the Prometheus metrics HTTP server in a background thread."""
    start_http_server(port)
    logger.info("Prometheus metrics server started on port %d", port)


def update_metrics(results: dict):
    """Update Prometheus metrics with detection results."""
    if not results or "containers" not in results:
        return

    summary = results.get("summary", {})
    DETECTION_TOTAL.set(summary.get("total", 0))
    DETECTION_ZOMBIES.set(summary.get("zombies", 0))
    DETECTION_POTENTIAL.set(summary.get("potential_zombies", 0))
    DETECTION_NORMAL.set(summary.get("normal", 0))

    for c in results["containers"]:
        ns = c.get("namespace", "")
        pod = c.get("pod", "")
        container = c.get("container", "")
        labels = [ns, pod, container]

        ZOMBIE_SCORE.labels(*labels).set(c.get("score", 0))

        classification = c.get("classification", "normal")
        ZOMBIE_CLASSIFICATION.labels(*labels).set(1 if classification == "zombie" else 0)
        ZOMBIE_POTENTIAL.labels(*labels).set(1 if classification == "potential_zombie" else 0)

        rules = c.get("rules", {})
        for rule_name, rule_score in rules.items():
            ZOMBIE_RULE_SCORE.labels(ns, pod, container, rule_name).set(rule_score)

    logger.info(
        "Metrics updated: %d containers (%d zombie, %d potential, %d normal)",
        summary.get("total", 0),
        summary.get("zombies", 0),
        summary.get("potential_zombies", 0),
        summary.get("normal", 0),
    )


def update_energy_metrics(energy_results: dict):
    """
    Update Isolation Forest comparison and energy waste metrics.

    """
    # Energy waste metrics
    for item in energy_results.get("test_cluster", {}).get("items", []):
        container = item["container"]
        ENERGY_WASTE_WATTS.labels(container=container).set(item.get("total_power_w", 0.0))
        MONTHLY_COST_WASTE_USD.labels(container=container).set(item.get("monthly_cost_usd", 0.0))

    logger.info("Comparison and energy metrics updated")
