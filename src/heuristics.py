"""
Heuristic rules engine for zombie container detection.

Implements 5 weighted rules that analyse temporal patterns in CPU, memory,
and network metrics to classify containers as zombie, potential zombie, or normal.

Rule 1: Sustained Low CPU with Memory Allocation (35%)
Rule 2: Memory Leak Pattern (25%)
Rule 3: Stuck Process Pattern (15%)
Rule 4: Network Timeout Pattern (15%)
Rule 5: Resource Ratio Imbalance (10%)
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Rule weights (must sum to 1.0)
RULE_WEIGHTS = {
    "rule1_low_cpu": 0.35,
    "rule2_memory_leak": 0.25,
    "rule3_stuck_process": 0.15,
    "rule4_network_timeout": 0.15,
    "rule5_resource_imbalance": 0.10,
}

# Thresholds
THRESHOLDS = {
    # Rule 1
    "low_cpu_percent": 5.0,
    "low_cpu_duration_minutes": 30,
    "network_negligible_bytes_per_sec": 100.0,
    # Rule 2
    "very_low_cpu_percent": 1.0,
    "memory_increase_percent": 5.0,
    "memory_leak_duration_minutes": 60,
    # Rule 3
    "cpu_spike_percent": 5.0,
    "spike_max_duration_sec": 30,
    "idle_after_spike_cpu_percent": 2.0,
    "idle_after_spike_min_minutes": 8,
    "min_spike_repetitions": 3,
    # Rule 4
    "network_timeout_min_rate": 1.0,       # minimum avg B/s to detect retry traffic
    "network_timeout_max_rate": 200.0,     # maximum avg B/s (above this = real service)
    # Rule 5
    "memory_min_allocation_mb": 500,
    "memory_usage_ratio_max": 0.10,
    "resource_imbalance_cpu_max": 1.0,
    "resource_imbalance_duration_minutes": 60,
}

# Classification thresholds
ZOMBIE_THRESHOLD = 60.0
POTENTIAL_ZOMBIE_THRESHOLD = 30.0


def analyse_container(metrics: dict, resource_limits: dict) -> dict:
    """
    Analyse a container's metrics and return zombie score with per-rule breakdown.

    Args:
        metrics: dict with keys 'cpu', 'memory', 'network_rx', 'network_tx' (each pd.Series)
        resource_limits: dict with 'cpu_limit' and 'memory_limit' (floats or None)

    Returns:
        dict with 'score', 'classification', 'rules' breakdown, and 'details'
    """
    rules = {}
    details = {}

    rules["rule1_low_cpu"], details["rule1_low_cpu"] = _rule1_sustained_low_cpu(metrics)
    rules["rule2_memory_leak"], details["rule2_memory_leak"] = _rule2_memory_leak(metrics)
    rules["rule3_stuck_process"], details["rule3_stuck_process"] = _rule3_stuck_process(metrics)
    rules["rule4_network_timeout"], details["rule4_network_timeout"] = _rule4_network_timeout(metrics)
    rules["rule5_resource_imbalance"], details["rule5_resource_imbalance"] = _rule5_resource_imbalance(
        metrics, resource_limits
    )

    # Compute weighted composite score (0-100)
    # Use weighted sum + boost from strongest rule to avoid dilution
    # when a single rule strongly indicates zombie behaviour
    weighted_sum = sum(rules[k] * RULE_WEIGHTS[k] for k in RULE_WEIGHTS)
    max_rule_score = max(rules.values()) if rules else 0.0
    boosted = weighted_sum + 0.3 * max_rule_score
    composite = min(100.0, max(0.0, min(1.0, boosted) * 100.0))

    if composite >= ZOMBIE_THRESHOLD:
        classification = "zombie"
    elif composite >= POTENTIAL_ZOMBIE_THRESHOLD:
        classification = "potential_zombie"
    else:
        classification = "normal"

    return {
        "score": round(composite, 2),
        "classification": classification,
        "rules": {k: round(v, 4) for k, v in rules.items()},
        "rule_weights": RULE_WEIGHTS,
        "details": details,
    }


def _rule1_sustained_low_cpu(metrics: dict) -> tuple:
    """
    Rule 1: Sustained Low CPU with Memory Allocation (35% weight).
    Detects CPU < 5% for > 30 minutes while memory remains stable/increasing
    and network activity is negligible.
    """
    cpu = metrics.get("cpu", pd.Series(dtype=float))
    mem = metrics.get("memory", pd.Series(dtype=float))
    net_rx = metrics.get("network_rx", pd.Series(dtype=float))
    net_tx = metrics.get("network_tx", pd.Series(dtype=float))

    details = {"triggered": False, "reason": "insufficient data"}

    if cpu.empty or len(cpu) < 10:
        return 0.0, details

    # Convert CPU to percentage (rate gives cores, *100 for percent)
    cpu_pct = cpu * 100.0

    # Check for ANY significant CPU activity in the window — if the container
    # had a meaningful spike at any point, it may be a legitimate workload
    # (cron job, batch processor, cold standby). Reduce score proportionally.
    max_cpu_in_window = cpu_pct.max()
    if max_cpu_in_window > THRESHOLDS["low_cpu_percent"] * 3:
        details = {
            "triggered": False,
            "reason": f"CPU spike detected in window (max={max_cpu_in_window:.2f}%), likely active workload",
            "max_cpu_pct": round(float(max_cpu_in_window), 4),
        }
        return 0.0, details

    # Calculate fraction of time CPU was below threshold
    low_cpu_mask = cpu_pct < THRESHOLDS["low_cpu_percent"]
    low_cpu_fraction = low_cpu_mask.sum() / len(cpu_pct) if len(cpu_pct) > 0 else 0

    # Duration check: need sustained low CPU
    total_duration_minutes = _series_duration_minutes(cpu)
    low_cpu_minutes = low_cpu_fraction * total_duration_minutes

    if low_cpu_minutes < THRESHOLDS["low_cpu_duration_minutes"]:
        details = {
            "triggered": False,
            "reason": f"low CPU duration {low_cpu_minutes:.1f}min < {THRESHOLDS['low_cpu_duration_minutes']}min threshold",
            "low_cpu_fraction": round(low_cpu_fraction, 4),
            "avg_cpu_pct": round(cpu_pct.mean(), 4),
        }
        return 0.0, details

    # Check memory is stable or increasing (not freed)
    # For small containers (< 10MB), percentage fluctuations are normal,
    # so also check absolute decrease to avoid false negatives
    mem_stable = True
    mem_trend = 0.0
    if not mem.empty and len(mem) >= 2:
        mem_start = mem.iloc[:max(1, len(mem) // 10)].mean()
        mem_end = mem.iloc[-max(1, len(mem) // 10):].mean()
        if mem_start > 0:
            mem_trend = (mem_end - mem_start) / mem_start
            mem_decrease_bytes = max(0, mem_start - mem_end)
            # Stable if: less than 5% decrease OR less than 1MB absolute decrease
            mem_stable = mem_trend >= -0.05 or mem_decrease_bytes < 1_000_000

    # Check network is negligible
    net_negligible = True
    avg_net = 0.0
    if not net_rx.empty and not net_tx.empty:
        avg_net = (net_rx.mean() + net_tx.mean())
        net_negligible = avg_net < THRESHOLDS["network_negligible_bytes_per_sec"]

    if not mem_stable:
        details = {
            "triggered": False,
            "reason": "memory is decreasing (container may be actively freeing resources)",
            "mem_trend_pct": round(mem_trend * 100, 2),
        }
        return 0.0, details

    # Score: higher for longer low-CPU duration and lower CPU values
    duration_score = min(1.0, low_cpu_minutes / 60.0)  # max at 60 min
    cpu_score = 1.0 - (cpu_pct.mean() / THRESHOLDS["low_cpu_percent"]) if cpu_pct.mean() < THRESHOLDS["low_cpu_percent"] else 0.0
    net_score = 1.0 if net_negligible else 0.3

    score = duration_score * 0.4 + cpu_score * 0.4 + net_score * 0.2

    details = {
        "triggered": True,
        "avg_cpu_pct": round(cpu_pct.mean(), 4),
        "low_cpu_fraction": round(low_cpu_fraction, 4),
        "low_cpu_minutes": round(low_cpu_minutes, 1),
        "mem_trend_pct": round(mem_trend * 100, 2),
        "avg_network_bytes_sec": round(avg_net, 2),
        "network_negligible": net_negligible,
        "duration_score": round(duration_score, 4),
        "cpu_score": round(cpu_score, 4),
    }

    return min(1.0, max(0.0, score)), details


def _rule2_memory_leak(metrics: dict) -> tuple:
    """
    Rule 2: Memory Leak Pattern (25% weight).
    Detects memory increasing > 5% over 1 hour while CPU < 1%.
    """
    cpu = metrics.get("cpu", pd.Series(dtype=float))
    mem = metrics.get("memory", pd.Series(dtype=float))

    details = {"triggered": False, "reason": "insufficient data"}

    if cpu.empty or mem.empty or len(mem) < 10:
        return 0.0, details

    cpu_pct = cpu * 100.0
    avg_cpu = cpu_pct.mean()

    # CPU must be very low
    if avg_cpu > THRESHOLDS["very_low_cpu_percent"]:
        details = {
            "triggered": False,
            "reason": f"avg CPU {avg_cpu:.2f}% > {THRESHOLDS['very_low_cpu_percent']}% threshold",
        }
        return 0.0, details

    # Calculate memory increase over the window
    window_size = max(1, len(mem) // 10)
    mem_start = mem.iloc[:window_size].mean()
    mem_end = mem.iloc[-window_size:].mean()

    if mem_start <= 0:
        details = {"triggered": False, "reason": "no initial memory data"}
        return 0.0, details

    mem_increase_pct = ((mem_end - mem_start) / mem_start) * 100.0

    if mem_increase_pct < THRESHOLDS["memory_increase_percent"]:
        details = {
            "triggered": False,
            "reason": f"memory increase {mem_increase_pct:.2f}% < {THRESHOLDS['memory_increase_percent']}% threshold",
            "mem_start_mb": round(mem_start / 1e6, 2),
            "mem_end_mb": round(mem_end / 1e6, 2),
        }
        return 0.0, details

    # Check for monotonic increase (leak pattern)
    # Split into quartiles and check each is higher than previous
    n = len(mem)
    quartiles = [mem.iloc[i * n // 4:(i + 1) * n // 4].mean() for i in range(4)]
    monotonic_count = sum(1 for i in range(1, len(quartiles)) if quartiles[i] > quartiles[i - 1])
    monotonic_score = monotonic_count / 3.0  # 0 to 1

    # Score based on leak magnitude and monotonicity
    magnitude_score = min(1.0, mem_increase_pct / 20.0)  # max at 20% increase
    score = magnitude_score * 0.6 + monotonic_score * 0.4

    details = {
        "triggered": True,
        "avg_cpu_pct": round(avg_cpu, 4),
        "mem_start_mb": round(mem_start / 1e6, 2),
        "mem_end_mb": round(mem_end / 1e6, 2),
        "mem_increase_pct": round(mem_increase_pct, 2),
        "monotonic_score": round(monotonic_score, 4),
        "magnitude_score": round(magnitude_score, 4),
    }

    return min(1.0, max(0.0, score)), details


def _rule3_stuck_process(metrics: dict) -> tuple:
    """
    Rule 3: Stuck Process Pattern (15% weight).
    Detects brief CPU spikes followed by extended idle periods, repeated 3+ times.
    Characteristic of a process stuck in a retry loop.
    """
    cpu = metrics.get("cpu", pd.Series(dtype=float))

    details = {"triggered": False, "reason": "insufficient data"}

    if cpu.empty or len(cpu) < 20:
        return 0.0, details

    cpu_pct = cpu * 100.0
    spike_threshold = THRESHOLDS["cpu_spike_percent"]
    idle_threshold = THRESHOLDS["idle_after_spike_cpu_percent"]

    # Detect spikes: periods where CPU > spike_threshold
    is_spike = cpu_pct > spike_threshold
    is_idle = cpu_pct < idle_threshold

    # Find spike-then-idle transitions
    spike_idle_transitions = 0
    in_spike = False
    spike_count = 0
    last_spike_end = None

    for i in range(len(cpu_pct)):
        if is_spike.iloc[i] and not in_spike:
            in_spike = True
            spike_count += 1
        elif not is_spike.iloc[i] and in_spike:
            in_spike = False
            last_spike_end = i

        if last_spike_end is not None and i > last_spike_end:
            # Check if we've been idle long enough after the spike
            idle_window = cpu_pct.iloc[last_spike_end:i + 1]
            if len(idle_window) > 0 and idle_window.mean() < idle_threshold:
                idle_duration = _series_duration_minutes(idle_window)
                if idle_duration >= THRESHOLDS["idle_after_spike_min_minutes"]:
                    spike_idle_transitions += 1
                    last_spike_end = None

    if spike_idle_transitions < THRESHOLDS["min_spike_repetitions"]:
        details = {
            "triggered": False,
            "reason": f"spike-idle transitions {spike_idle_transitions} < {THRESHOLDS['min_spike_repetitions']} minimum",
            "total_spikes": spike_count,
        }
        return 0.0, details

    # Score based on number of repetitions
    repetition_score = min(1.0, spike_idle_transitions / 5.0)

    # Also check overall CPU is low (mostly idle)
    overall_low = 1.0 if cpu_pct.median() < idle_threshold else 0.5

    score = repetition_score * 0.7 + overall_low * 0.3

    details = {
        "triggered": True,
        "spike_idle_transitions": spike_idle_transitions,
        "total_spikes": spike_count,
        "median_cpu_pct": round(cpu_pct.median(), 4),
        "repetition_score": round(repetition_score, 4),
    }

    return min(1.0, max(0.0, score)), details


def _rule4_network_timeout(metrics: dict) -> tuple:
    """
    Rule 4: Network Timeout Pattern (15% weight).
    Detects containers with very low CPU but persistent, low-volume network
    traffic — characteristic of a process retrying connections to a dead service.

    With rate()[5m] smoothing, periodic retry bursts appear as constant low-rate
    traffic, so we detect the pattern by: near-zero CPU + low but persistent
    non-zero network activity + no meaningful work being done.
    """
    cpu = metrics.get("cpu", pd.Series(dtype=float))
    net_rx = metrics.get("network_rx", pd.Series(dtype=float))
    net_tx = metrics.get("network_tx", pd.Series(dtype=float))

    details = {"triggered": False, "reason": "insufficient data"}

    if net_tx.empty or len(net_tx) < 10 or cpu.empty or len(cpu) < 10:
        return 0.0, details

    cpu_pct = cpu * 100.0

    # CPU must be very low (< 1%) — the container does almost no computation
    if cpu_pct.mean() > THRESHOLDS["very_low_cpu_percent"]:
        details = {
            "triggered": False,
            "reason": f"avg CPU {cpu_pct.mean():.2f}% > {THRESHOLDS['very_low_cpu_percent']}% threshold",
        }
        return 0.0, details

    # Combine TX and RX for total network activity
    # Use .values to avoid DatetimeIndex misalignment (RX and TX may have
    # slightly different timestamps from Prometheus scrapes)
    net_total = net_tx
    if not net_rx.empty and len(net_rx) == len(net_tx):
        net_total = pd.Series(net_rx.values + net_tx.values, index=net_tx.index)

    avg_net = float(net_total.mean())

    # Network must be non-zero: there IS some traffic (retry attempts)
    if avg_net < THRESHOLDS["network_timeout_min_rate"]:
        details = {
            "triggered": False,
            "reason": f"avg network {avg_net:.2f} B/s < {THRESHOLDS['network_timeout_min_rate']} B/s (no retry traffic)",
        }
        return 0.0, details

    # Network must be low-volume: not a real service doing useful work
    if avg_net > THRESHOLDS["network_timeout_max_rate"]:
        details = {
            "triggered": False,
            "reason": f"avg network {avg_net:.2f} B/s > {THRESHOLDS['network_timeout_max_rate']} B/s (significant traffic)",
        }
        return 0.0, details

    # Check that the network traffic is persistent throughout the window
    # (not just a one-off event). Most samples should have some activity.
    active_mask = net_total > 0.5
    active_fraction = float(active_mask.sum()) / len(net_total)

    if active_fraction < 0.3:
        details = {
            "triggered": False,
            "reason": f"network active only {active_fraction:.0%} of window (need >30%)",
        }
        return 0.0, details

    # Score based on: low CPU + persistent low network = retry pattern
    cpu_score = 1.0 - (cpu_pct.mean() / THRESHOLDS["very_low_cpu_percent"])
    persistence_score = min(1.0, active_fraction / 0.8)
    # Lower average network = more likely dead-service retries (just DNS lookups)
    volume_score = 1.0 - min(1.0, avg_net / THRESHOLDS["network_timeout_max_rate"])

    score = cpu_score * 0.4 + persistence_score * 0.3 + volume_score * 0.3

    details = {
        "triggered": True,
        "avg_cpu_pct": round(float(cpu_pct.mean()), 4),
        "avg_network_bytes_sec": round(avg_net, 2),
        "active_fraction": round(active_fraction, 4),
        "cpu_score": round(cpu_score, 4),
        "persistence_score": round(persistence_score, 4),
        "volume_score": round(volume_score, 4),
    }

    return min(1.0, max(0.0, score)), details


def _rule5_resource_imbalance(metrics: dict, resource_limits: dict) -> tuple:
    """
    Rule 5: Resource Ratio Imbalance (10% weight).
    Detects containers with high memory allocation but very low usage,
    combined with near-zero CPU.
    """
    cpu = metrics.get("cpu", pd.Series(dtype=float))
    mem = metrics.get("memory", pd.Series(dtype=float))

    details = {"triggered": False, "reason": "insufficient data"}

    if cpu.empty or mem.empty:
        return 0.0, details

    cpu_pct = cpu * 100.0
    mem_limit = resource_limits.get("memory_limit")

    # Need resource limits to assess imbalance
    if mem_limit is None or mem_limit <= 0:
        details = {"triggered": False, "reason": "no memory limit set"}
        return 0.0, details

    # Check CPU is very low
    avg_cpu = cpu_pct.mean()
    if avg_cpu > THRESHOLDS["resource_imbalance_cpu_max"]:
        details = {
            "triggered": False,
            "reason": f"avg CPU {avg_cpu:.2f}% > {THRESHOLDS['resource_imbalance_cpu_max']}% threshold",
        }
        return 0.0, details

    # Check memory allocation is significant
    mem_limit_mb = mem_limit / (1024 * 1024)
    if mem_limit_mb < THRESHOLDS["memory_min_allocation_mb"]:
        details = {
            "triggered": False,
            "reason": f"memory limit {mem_limit_mb:.0f}MB < {THRESHOLDS['memory_min_allocation_mb']}MB threshold",
        }
        return 0.0, details

    # Check memory usage ratio is very low
    avg_mem = mem.mean()
    usage_ratio = avg_mem / mem_limit if mem_limit > 0 else 0

    if usage_ratio > THRESHOLDS["memory_usage_ratio_max"]:
        details = {
            "triggered": False,
            "reason": f"memory usage ratio {usage_ratio:.2%} > {THRESHOLDS['memory_usage_ratio_max']:.0%} threshold",
        }
        return 0.0, details

    # Check this has been sustained
    duration = _series_duration_minutes(cpu)
    if duration < THRESHOLDS["resource_imbalance_duration_minutes"]:
        details = {
            "triggered": False,
            "reason": f"duration {duration:.1f}min < {THRESHOLDS['resource_imbalance_duration_minutes']}min threshold",
        }
        return 0.0, details

    # Score: higher for greater imbalance
    imbalance_score = 1.0 - (usage_ratio / THRESHOLDS["memory_usage_ratio_max"])
    cpu_score = 1.0 - (avg_cpu / THRESHOLDS["resource_imbalance_cpu_max"])
    score = imbalance_score * 0.6 + cpu_score * 0.4

    details = {
        "triggered": True,
        "avg_cpu_pct": round(avg_cpu, 4),
        "mem_limit_mb": round(mem_limit_mb, 2),
        "avg_mem_usage_mb": round(avg_mem / 1e6, 2),
        "usage_ratio": round(usage_ratio, 4),
        "imbalance_score": round(imbalance_score, 4),
    }

    return min(1.0, max(0.0, score)), details


def _series_duration_minutes(series: pd.Series) -> float:
    """Calculate the duration of a time series in minutes."""
    if series.empty or len(series) < 2:
        return 0.0
    if hasattr(series.index, 'dtype') and pd.api.types.is_datetime64_any_dtype(series.index):
        duration = (series.index[-1] - series.index[0]).total_seconds() / 60.0
        return max(0.0, duration)
    # Fallback: assume 15-second intervals
    return (len(series) - 1) * 15.0 / 60.0
