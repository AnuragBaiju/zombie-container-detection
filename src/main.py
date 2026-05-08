"""
CLI entry point for zombie container detector.
Supports one-shot and continuous monitoring modes.
"""

import argparse
import logging
import sys
import time

from .detector import ZombieDetector, format_text_output, format_json_output
from .exporter import start_metrics_server, update_metrics, update_energy_metrics
from .energy_impact import calculate_cluster_impact

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Heuristic-based zombie container detector for Kubernetes"
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://prometheus-server.monitoring.svc.cluster.local:9090",
        help="Prometheus server URL (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Analysis window in minutes (default: %(default)s)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=70.0,
        help="Zombie score threshold 0-100 (default: %(default)s)",
    )
    parser.add_argument(
        "--exclude-namespaces",
        default="kube-system,kube-public,kube-node-lease,monitoring",
        help="Comma-separated namespaces to exclude (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Show detailed per-rule breakdown",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=8080,
        help="Port for Prometheus metrics exporter (default: %(default)s)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run in continuous monitoring mode",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Interval between checks in continuous mode, seconds (default: %(default)s)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    exclude_ns = [ns.strip() for ns in args.exclude_namespaces.split(",")]

    detector = ZombieDetector(
        prometheus_url=args.prometheus_url,
        duration_minutes=args.duration,
        exclude_namespaces=exclude_ns,
    )

    if args.continuous:
        # Start Prometheus metrics exporter for Grafana
        start_metrics_server(args.metrics_port)
        logger.info("Starting continuous monitoring (interval: %ds)", args.interval)

        # Export energy waste metrics for dashboard
        try:
            energy = calculate_cluster_impact()
            update_energy_metrics(energy)
        except Exception as e:
            logger.warning("Energy metrics update failed (non-critical): %s", e)

        while True:
            try:
                results = detector.detect(threshold=args.threshold)
                update_metrics(results)
                if args.output == "json":
                    print(format_json_output(results))
                else:
                    print(format_text_output(results, show_details=args.details))
                sys.stdout.flush()
            except Exception as e:
                logger.error("Detection cycle failed: %s", e)
            time.sleep(args.interval)
    else:
        results = detector.detect(threshold=args.threshold)
        if args.output == "json":
            print(format_json_output(results))
        else:
            print(format_text_output(results, show_details=args.details))

        # Exit with code 1 if zombies found (useful for CI/CD)
        if results["summary"]["zombies"] > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
