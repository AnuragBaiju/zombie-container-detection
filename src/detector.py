"""
Main zombie container detector.
Orchestrates metrics collection, heuristic analysis, and result reporting.
"""

import logging
import json
from datetime import datetime
from .metrics_collector import MetricsCollector
from .heuristics import analyse_container

logger = logging.getLogger(__name__)


class ZombieDetector:
    """Detects zombie containers in a Kubernetes cluster using heuristic rules."""

    def __init__(self, prometheus_url: str, duration_minutes: int = 60,
                 exclude_namespaces: list = None):
        self.collector = MetricsCollector(prometheus_url)
        self.duration_minutes = duration_minutes
        self.exclude_namespaces = exclude_namespaces or [
            "kube-system", "kube-public", "kube-node-lease", "monitoring"
        ]

    def detect(self, threshold: float = 70.0) -> dict:
        """
        Run zombie detection on all containers in the cluster.

        Returns:
            dict with 'timestamp', 'containers' list, and 'summary'
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        containers = self.collector.get_running_containers(self.exclude_namespaces)

        if not containers:
            logger.warning("No containers found to analyse")
            return {
                "timestamp": timestamp,
                "containers": [],
                "summary": {"total": 0, "zombies": 0, "potential_zombies": 0, "normal": 0},
            }

        results = []
        for c in containers:
            ns, pod, container = c["namespace"], c["pod"], c["container"]
            logger.info("Analysing %s/%s/%s", ns, pod, container)

            try:
                metrics = self.collector.get_container_metrics(
                    ns, pod, container, self.duration_minutes
                )
                limits = self.collector.get_container_resource_limits(ns, pod, container)
                analysis = analyse_container(metrics, limits)

                result = {
                    "namespace": ns,
                    "pod": pod,
                    "container": container,
                    "score": analysis["score"],
                    "classification": analysis["classification"],
                    "rules": analysis["rules"],
                    "details": analysis["details"],
                }
                results.append(result)

            except Exception as e:
                logger.error("Error analysing %s/%s/%s: %s", ns, pod, container, e)
                results.append({
                    "namespace": ns,
                    "pod": pod,
                    "container": container,
                    "score": 0.0,
                    "classification": "error",
                    "error": str(e),
                })

        # Summary
        zombies = [r for r in results if r.get("classification") == "zombie"]
        potential = [r for r in results if r.get("classification") == "potential_zombie"]
        normal = [r for r in results if r.get("classification") == "normal"]

        return {
            "timestamp": timestamp,
            "containers": sorted(results, key=lambda x: x.get("score", 0), reverse=True),
            "summary": {
                "total": len(results),
                "zombies": len(zombies),
                "potential_zombies": len(potential),
                "normal": len(normal),
                "errors": len(results) - len(zombies) - len(potential) - len(normal),
            },
        }


def format_text_output(results: dict, show_details: bool = False) -> str:
    """Format detection results as human-readable text."""
    lines = []
    lines.append("=" * 70)
    lines.append("ZOMBIE CONTAINER DETECTION REPORT")
    lines.append(f"Timestamp: {results['timestamp']}")
    lines.append("=" * 70)

    summary = results["summary"]
    lines.append(f"\nSummary: {summary['total']} containers analysed")
    lines.append(f"  Zombies:           {summary['zombies']}")
    lines.append(f"  Potential Zombies: {summary['potential_zombies']}")
    lines.append(f"  Normal:            {summary['normal']}")
    if summary.get("errors", 0) > 0:
        lines.append(f"  Errors:            {summary['errors']}")
    lines.append("")

    for c in results["containers"]:
        classification = c.get("classification", "unknown").upper()
        score = c.get("score", 0)

        if classification == "ZOMBIE":
            marker = "[ZOMBIE]"
        elif classification == "POTENTIAL_ZOMBIE":
            marker = "[POTENTIAL]"
        elif classification == "ERROR":
            marker = "[ERROR]"
        else:
            marker = "[NORMAL]"

        lines.append(f"{marker} {c['namespace']}/{c['pod']}/{c['container']} — Score: {score:.1f}/100")

        if show_details and "rules" in c:
            for rule_name, rule_score in c["rules"].items():
                triggered = ""
                if "details" in c and rule_name in c["details"]:
                    triggered = " (triggered)" if c["details"][rule_name].get("triggered") else ""
                lines.append(f"    {rule_name}: {rule_score:.4f}{triggered}")

            if "details" in c:
                for rule_name, detail in c["details"].items():
                    if detail.get("triggered"):
                        lines.append(f"    Details for {rule_name}:")
                        for k, v in detail.items():
                            if k != "triggered":
                                lines.append(f"      {k}: {v}")
        lines.append("")

    return "\n".join(lines)


def format_json_output(results: dict) -> str:
    """Format detection results as JSON."""
    return json.dumps(results, indent=2)
