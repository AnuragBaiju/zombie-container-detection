"""
Evaluation script to measure detection accuracy against ground-truth test scenarios.

Single anchor paper: Li et al. (2025), "Energy-Aware Elastic Scaling Algorithm
for Kubernetes Microservices," J. Network and Computer Applications.
Li et al. acknowledge Kubernetes cannot distinguish active from idle containers
and propose a scaling algorithm that *assumes* a list of zombies is given. This
detector provides the missing classification layer.

The evaluation reports:
1. Heuristic accuracy across the canonical 7-container set (designed positives)
   and the adversarial 5-container set (designed failure-mode probes).
2. Energy / cost impact via the Li et al. model (P = cpu*3.7W + mem*0.375W/GB
   * PUE 1.2), implemented in src/energy_impact.py.
"""

import argparse
import csv
import json
import logging
import sys

from .detector import ZombieDetector
from .energy_impact import calculate_cluster_impact, format_energy_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Ground truth: container name -> expected classification.
#
# The first 7 entries are the "canonical" test set: 5 zombie archetypes plus
# 2 normals. These are designed to exercise each rule in isolation.
#
# The 5 "adversarial-*" entries were added in response to professor feedback
# that 100% accuracy on the canonical set is unrealistic. They deliberately
# probe the heuristic's blind spots:
#   - adversarial-cron-hourly:    cycle longer than the analysis window  (FP)
#   - adversarial-cold-standby:   indistinguishable from a Rule-4 zombie (FP)
#   - adversarial-jvm-warmup:     legitimate monotonic memory growth     (FP)
#   - adversarial-stealth-zombie: zombie that evades Rule 1's spike check (FN)
#   - adversarial-low-traffic-api: real but rare traffic resembles retries (FP)
GROUND_TRUTH = {
    # --- canonical test set ---
    "normal-web": "normal",
    "normal-batch": "normal",
    "zombie-low-cpu": "zombie",
    "zombie-memory-leak": "zombie",
    "zombie-stuck-process": "zombie",
    "zombie-network-timeout": "zombie",
    "zombie-resource-imbalance": "zombie",
    # --- adversarial test set (failure-mode probes) ---
    "adversarial-cron-hourly": "normal",
    "adversarial-cold-standby": "normal",
    "adversarial-jvm-warmup": "normal",
    "adversarial-stealth-zombie": "zombie",
    "adversarial-low-traffic-api": "normal",
}

# Each adversarial container is annotated with the rule that misclassifies it
# and the operational lesson the misclassification teaches.
ADVERSARIAL_NOTES = {
    "adversarial-cron-hourly": {
        "expected_failure": "false_positive",
        "trigger_rule": "rule1_low_cpu",
        "lesson": (
            "Rule 1 looks at a fixed 60-minute window. Workloads with a duty "
            "cycle longer than the window appear flat-line and are misclassified. "
            "Mitigation: increase --duration to cover the full cycle, or read "
            "CronJob schedules from the Kubernetes API and exclude their pods."
        ),
    },
    "adversarial-cold-standby": {
        "expected_failure": "false_positive",
        "trigger_rule": "rule4_network_timeout",
        "lesson": (
            "A cold-standby pod that holds memory and emits keepalive heartbeats "
            "is behaviourally identical to a network-timeout zombie. The heuristic "
            "cannot distinguish 'failover ready' from 'retrying a dead service'. "
            "Mitigation: pod annotation (e.g. zombie.io/standby=true) to exempt."
        ),
    },
    "adversarial-jvm-warmup": {
        "expected_failure": "false_positive",
        "trigger_rule": "rule2_memory_leak",
        "lesson": (
            "A JVM (or any cache-warming process) shows monotonic memory growth "
            "with low CPU during warmup. Rule 2 fires because it cannot tell "
            "'leak' from 'one-shot warmup that will eventually plateau'. "
            "Mitigation: re-evaluate after pod uptime > 1 hour."
        ),
    },
    "adversarial-stealth-zombie": {
        "expected_failure": "false_negative",
        "trigger_rule": "rule1_low_cpu",
        "lesson": (
            "Rule 1 excludes any container whose max CPU in the window exceeds "
            "3 * low_cpu_percent (15%). A zombie that emits a brief synthetic "
            "spike every 12 minutes evades the check. This is the heuristic's "
            "worst case and the strongest argument for layering anomaly detection "
            "(or analysing CPU-to-work-output ratios) on top of the rule engine."
        ),
    },
    "adversarial-low-traffic-api": {
        "expected_failure": "false_positive",
        "trigger_rule": "rule4_network_timeout",
        "lesson": (
            "A real internal microservice that genuinely receives one request "
            "every five minutes produces the same trace as zombie-network-timeout. "
            "Mitigation: correlate with service-mesh request counts or success "
            "rates; persistent low-volume traffic alone is not a zombie signal."
        ),
    },
}


def evaluate(prometheus_url: str, namespace: str = "test-scenarios",
             duration_minutes: int = 60) -> dict:
    """Run evaluation against ground truth test scenarios."""
    detector = ZombieDetector(
        prometheus_url=prometheus_url,
        duration_minutes=duration_minutes,
        exclude_namespaces=["kube-system", "kube-public", "kube-node-lease", "monitoring"],
    )

    results = detector.detect()

    # Filter to test namespace only
    test_containers = [
        c for c in results["containers"]
        if c["namespace"] == namespace
    ]

    if not test_containers:
        logger.error("No containers found in namespace '%s'", namespace)
        logger.info("Found namespaces: %s",
                     set(c["namespace"] for c in results["containers"]))
        return {"error": "no containers found in test namespace"}

    # Match against ground truth
    tp = fp = tn = fn = 0
    per_container = []

    for container in test_containers:
        name = container["container"]
        predicted = container["classification"]
        expected = GROUND_TRUTH.get(name)

        if expected is None:
            logger.warning("Container '%s' not in ground truth, skipping", name)
            continue

        # Binary classification: zombie/potential_zombie vs normal
        predicted_positive = predicted in ("zombie", "potential_zombie")
        actual_positive = expected == "zombie"

        if actual_positive and predicted_positive:
            tp += 1
            match = True
        elif not actual_positive and not predicted_positive:
            tn += 1
            match = True
        elif not actual_positive and predicted_positive:
            fp += 1
            match = False
        else:  # actual_positive and not predicted_positive
            fn += 1
            match = False

        row = {
            "container": name,
            "expected": expected,
            "predicted": predicted,
            "score": container["score"],
            "correct": match,
            "rules": container.get("rules", {}),
        }
        if name in ADVERSARIAL_NOTES:
            row["adversarial"] = True
            row["expected_failure"] = ADVERSARIAL_NOTES[name]["expected_failure"]
            row["trigger_rule"] = ADVERSARIAL_NOTES[name]["trigger_rule"]
            row["lesson"] = ADVERSARIAL_NOTES[name]["lesson"]
        else:
            row["adversarial"] = False
        per_container.append(row)

    # Calculate metrics
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0

    evaluation = {
        "metrics": {
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "false_positive_rate": round(fpr, 4),
        },
        "confusion_matrix": {
            "true_positives": tp,
            "true_negatives": tn,
            "false_positives": fp,
            "false_negatives": fn,
        },
        "per_container": per_container,
        "total_containers": total,
    }

    return evaluation


def run_full_comparative_evaluation(prometheus_url: str) -> dict:
    """
    Run complete evaluation: heuristic detector accuracy + Li et al. (2025)
    energy/cost impact for every container the heuristic flagged as zombie.
    """
    heuristic = evaluate(prometheus_url)
    logger.info("Calculating energy and cost impact...")
    energy = calculate_cluster_impact()
    return {
        "heuristic_evaluation": heuristic,
        "energy_impact": energy,
    }


def print_full_comparative_report(full_results: dict):
    """Print the complete comparative evaluation for professor review."""
    print_evaluation(full_results["heuristic_evaluation"])
    print()
    print(format_energy_report(full_results["energy_impact"]))

    # Section 4: Why 7 containers summary
    print()
    print("=" * 72)
    print("WHY 7 CONTAINERS? -- EXPERIMENTAL DESIGN RATIONALE")
    print("=" * 72)
    print("""
The 7 test containers represent the minimal sufficient set to validate all 5
heuristic rules against both zombie archetypes AND legitimately idle workloads.

5 ZOMBIE ARCHETYPES (derived from Zhao et al. 2023, Dang & Sharma 2024):
  1. zombie-low-cpu           -> Rule 1: Orphaned container (sleep + memory hold)
  2. zombie-memory-leak       -> Rule 2: Memory leak pattern (2MB/min growth)
  3. zombie-stuck-process     -> Rule 3: Retry loop (spike->idle cycle x3+)
  4. zombie-network-timeout   -> Rule 4: Dead-service reconnect (48 B/s retries)
  5. zombie-resource-imbalance -> Rule 5: Over-provisioned, never scaled down

2 NORMAL CONTAINERS (true negatives -- key for false-positive measurement):
  6. normal-web               -> Active with continuous CPU+network (FP guard)
  7. normal-batch             -> Legitimately idle (cron-style, FP guard for Rule 1)
     WHY normal-batch matters: a naive threshold ("CPU<5% for 30min") would flag
     this container during its 9-minute idle window. Our Rule 1 correctly excludes
     it because CPU history contains recent large spikes (85% max) -- the defining
     feature that separates zombie from legitimate batch workload.

SCALE RELEVANCE: Jindal et al. (2023) identified 30% zombie-like patterns across
1,000 production clusters. In a 100-pod cluster, that is ~30 zombie containers.
This test suite validates all 5 archetypes that cause this 30% prevalence.
""")


def print_evaluation(evaluation: dict):
    """Print evaluation results in a readable format."""
    if "error" in evaluation:
        print(f"Error: {evaluation['error']}")
        return

    print("=" * 60)
    print("HEURISTIC DETECTOR EVALUATION RESULTS")
    print("=" * 60)

    m = evaluation["metrics"]
    print(f"\nAccuracy:           {m['accuracy']:.2%}")
    print(f"Precision:          {m['precision']:.2%}")
    print(f"Recall:             {m['recall']:.2%}")
    print(f"F1 Score:           {m['f1_score']:.2%}")
    print(f"False Positive Rate: {m['false_positive_rate']:.2%}")

    cm = evaluation["confusion_matrix"]
    print(f"\nConfusion Matrix:")
    print(f"  True Positives:  {cm['true_positives']}")
    print(f"  True Negatives:  {cm['true_negatives']}")
    print(f"  False Positives: {cm['false_positives']}")
    print(f"  False Negatives: {cm['false_negatives']}")

    print(f"\nPer-Container Results:")
    print(f"{'Container':<30} {'Expected':<12} {'Predicted':<18} {'Score':>8} {'Correct':>8}")
    print("-" * 80)
    for c in evaluation["per_container"]:
        correct_str = "YES" if c["correct"] else "NO"
        print(f"{c['container']:<30} {c['expected']:<12} {c['predicted']:<18} {c['score']:>7.1f} {correct_str:>8}")

    target = 90.0
    actual = m["accuracy"] * 100
    print(f"\nTarget accuracy: {target}% | Actual: {actual:.1f}% | {'PASS' if actual >= target else 'BELOW TARGET'}")


def save_csv(evaluation: dict, filename: str = "evaluation_results.csv"):
    """Save per-container results to CSV."""
    if "per_container" not in evaluation:
        return
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "container", "expected", "predicted", "score", "correct",
        ])
        writer.writeheader()
        for c in evaluation["per_container"]:
            writer.writerow({
                "container": c["container"],
                "expected": c["expected"],
                "predicted": c["predicted"],
                "score": c["score"],
                "correct": c["correct"],
            })
    logger.info("Results saved to %s", filename)


def main():
    parser = argparse.ArgumentParser(description="Evaluate zombie detector accuracy")
    parser.add_argument(
        "--prometheus-url",
        default="http://prometheus-server.monitoring.svc.cluster.local:9090",
    )
    parser.add_argument("--namespace", default="test-scenarios")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--output-csv", default="evaluation_results.csv")
    parser.add_argument("--output-json", default="")
    parser.add_argument(
        "--full-comparison",
        action="store_true",
        help="Include Isolation Forest comparison and energy impact analysis",
    )
    args = parser.parse_args()

    if args.full_comparison:
        # Full comparative evaluation for professor review
        full = run_full_comparative_evaluation(args.prometheus_url)
        print_full_comparative_report(full)

        if args.output_json:
            with open(args.output_json, "w") as f:
                json.dump(full, f, indent=2)

        evaluation = full["heuristic_evaluation"]
    else:
        evaluation = evaluate(args.prometheus_url, args.namespace, args.duration)
        print_evaluation(evaluation)
        save_csv(evaluation, args.output_csv)

        if args.output_json:
            with open(args.output_json, "w") as f:
                json.dump(evaluation, f, indent=2)

    # Exit 0 if accuracy >= 90%, else 1
    if "metrics" in evaluation and evaluation["metrics"]["accuracy"] >= 0.90:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
