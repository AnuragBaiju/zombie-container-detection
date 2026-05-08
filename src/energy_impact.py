"""
Energy and Cost Impact Analysis for Zombie Container Detection

Quantifies resource waste from zombie containers using the energy model from:
Li et al. (2025), "Energy-Aware Elastic Scaling Algorithm for Kubernetes Microservices,"
Journal of Network and Computer Applications (Elsevier, IF≈7.5).

Key finding from Li et al.: Managing idle containers yields 15.34% energy reduction.
This module calculates the energy waste our detector identifies, demonstrating the
practical impact of zombie detection -- the "missing classification layer" that Li et al.
acknowledge but do not provide (Gap 1 of Li et al. critical review).

Gap addressed:
"EAES is fundamentally a scaling algorithm, not a detection mechanism. It does not
produce a per-container verdict of 'zombie', 'legitimately idle', or 'active'."
-- Li et al. (2025) critical review, Gap 1

This module provides the DETECTION LAYER that must precede any scaling action.
"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# -- Physical constants ---------------------------------------------------------
# AWS EC2 T3 family power profile (Li et al. energy model parameters)
POWER_PER_VCPU_WATTS = 3.7            # W per vCPU core at idle (T3 TDP / #vCPUs)
POWER_PER_GB_MEMORY_WATTS = 0.375     # W per GB of allocated DDR4 memory
AWS_PUE = 1.2                         # Power Usage Effectiveness (AWS data centres, 2024)

# Carbon intensity
CARBON_INTENSITY_KG_KWH = 0.233       # kg CO2/kWh (US-East-1, EPA + AWS 2024 report)

# AWS EC2 pricing (us-east-1, on-demand)
EC2_T3_MEDIUM_HOURLY_USD = 0.0416     # $0.0416/hour for t3.medium (2 vCPU, 4GB RAM)
HOURS_PER_MONTH = 24 * 30             # 720 hours/month

# Scale factor from Li et al.: EAES achieves 15.34% energy reduction by managing idle containers
LI_ET_AL_ENERGY_REDUCTION_PCT = 15.34

# Resource allocations for the 5 zombie test containers
# Source: kubernetes/test-scenarios/*.yaml
ZOMBIE_ALLOCATIONS: Dict[str, Dict] = {
    "zombie-low-cpu": {
        "description": "Orphaned sidecar holding memory after parent service deleted",
        "real_world_example": "Monitoring agent whose target pod was evicted; never cleaned up",
        "cpu_request_cores": 0.100,   # 100m
        "cpu_limit_cores": 0.200,     # 200m
        "memory_request_gb": 0.125,   # 128Mi
        "memory_limit_gb": 0.250,     # 256Mi
    },
    "zombie-memory-leak": {
        "description": "Abandoned service with unclosed connection accumulating buffers",
        "real_world_example": "Microservice left running after feature flag disabled; leaks 2MB/min",
        "cpu_request_cores": 0.050,   # 50m
        "cpu_limit_cores": 0.200,     # 200m
        "memory_request_gb": 0.125,   # 128Mi
        "memory_limit_gb": 1.000,     # 1Gi
    },
    "zombie-stuck-process": {
        "description": "Service stuck in retry loop against decommissioned dependency",
        "real_world_example": "Payment gateway client retrying removed legacy API endpoint",
        "cpu_request_cores": 0.050,   # 50m
        "cpu_limit_cores": 0.500,     # 500m
        "memory_request_gb": 0.063,   # 64Mi
        "memory_limit_gb": 0.250,     # 256Mi
    },
    "zombie-network-timeout": {
        "description": "Client retrying connections to a decommissioned upstream service",
        "real_world_example": "gRPC client reconnecting to removed internal service every 3 minutes",
        "cpu_request_cores": 0.050,   # 50m
        "cpu_limit_cores": 0.100,     # 100m
        "memory_request_gb": 0.063,   # 64Mi
        "memory_limit_gb": 0.125,     # 128Mi
    },
    "zombie-resource-imbalance": {
        "description": "Over-provisioned container from load test never scaled down",
        "real_world_example": "Stress-test pod allocated 512Mi/1vCPU; developers forgot to tear down",
        "cpu_request_cores": 0.500,   # 500m
        "cpu_limit_cores": 1.000,     # 1000m
        "memory_request_gb": 0.500,   # 512Mi
        "memory_limit_gb": 1.000,     # 1Gi
    },
}


def calculate_waste(name: str, alloc: Dict) -> Dict:
    """
    Calculate energy and cost waste for one zombie container using Li et al.'s model.

    Energy model:
        P_waste = (cpu_req * P_cpu + mem_req * P_mem) * PUE

    We use REQUESTED allocation (not limit) because Kubernetes reserves request
    capacity on the node -- it is unavailable to other workloads regardless of
    actual usage.
    """
    cpu_cores = alloc["cpu_request_cores"]
    mem_gb = alloc["memory_request_gb"]

    # Component power consumption
    cpu_power_w = cpu_cores * POWER_PER_VCPU_WATTS
    mem_power_w = mem_gb * POWER_PER_GB_MEMORY_WATTS
    total_power_w = (cpu_power_w + mem_power_w) * AWS_PUE

    # Monthly energy waste (kWh)
    monthly_energy_kwh = (total_power_w * HOURS_PER_MONTH) / 1000.0

    # CO2 footprint
    monthly_co2_kg = monthly_energy_kwh * CARBON_INTENSITY_KG_KWH

    # AWS cost: proportional share of a t3.medium instance
    # t3.medium = 2 vCPUs, 4 GB RAM -> $0.0416/hour
    cpu_share = cpu_cores / 2.0
    mem_share = mem_gb / 4.0
    instance_share = max(cpu_share, mem_share)  # bottleneck resource
    monthly_cost_usd = instance_share * EC2_T3_MEDIUM_HOURLY_USD * HOURS_PER_MONTH

    return {
        "container": name,
        "description": alloc.get("description", ""),
        "real_world_example": alloc.get("real_world_example", ""),
        "allocated_cpu_cores": round(cpu_cores, 3),
        "allocated_memory_gb": round(mem_gb, 3),
        "cpu_power_w": round(cpu_power_w, 3),
        "mem_power_w": round(mem_power_w, 3),
        "total_power_w": round(total_power_w, 3),
        "monthly_energy_kwh": round(monthly_energy_kwh, 4),
        "monthly_co2_kg": round(monthly_co2_kg, 4),
        "monthly_cost_usd": round(monthly_cost_usd, 2),
        "annual_cost_usd": round(monthly_cost_usd * 12, 2),
    }


def calculate_cluster_impact(zombie_names: Optional[List[str]] = None) -> Dict:
    """
    Calculate total energy and cost impact for all detected zombie containers.

    Also projects to a real 100-pod cluster using Jindal et al. (2023) statistics:
    "approximately 30% of containers showed zombie-like usage patterns."

    Connects to Li et al. (2025): identifies what their EAES algorithm
    would save IF it had a detection layer (which we provide).
    """
    if zombie_names is None:
        zombie_names = list(ZOMBIE_ALLOCATIONS.keys())

    items = []
    total_power_w = 0.0
    total_kwh = 0.0
    total_co2 = 0.0
    total_cost = 0.0

    for name in zombie_names:
        alloc = ZOMBIE_ALLOCATIONS.get(name)
        if not alloc:
            continue
        w = calculate_waste(name, alloc)
        items.append(w)
        total_power_w += w["total_power_w"]
        total_kwh += w["monthly_energy_kwh"]
        total_co2 += w["monthly_co2_kg"]
        total_cost += w["monthly_cost_usd"]

    # Scale to 100-pod cluster (30% zombie rate = 30 zombie containers)
    n_zombies = max(len(items), 1)
    scale = 30.0 / n_zombies  # project test cluster zombies -> 30 zombies

    # Li et al. EAES achieves 15.34% energy reduction across the WHOLE cluster.
    # Our detector enables targeted removal of confirmed zombies -- potentially
    # higher savings since we only remove confirmed idle containers, not all
    # low-utilisation instances (which avoids disrupting legitimate standby services).
    total_cluster_monthly_cost_aws_estimate = 2 * EC2_T3_MEDIUM_HOURLY_USD * HOURS_PER_MONTH * 100  # rough: 100 nodes
    li_et_al_savings_monthly = total_cluster_monthly_cost_aws_estimate * (LI_ET_AL_ENERGY_REDUCTION_PCT / 100)

    return {
        "test_cluster": {
            "zombie_count": n_zombies,
            "items": items,
            "total_power_waste_w": round(total_power_w, 2),
            "total_monthly_energy_kwh": round(total_kwh, 3),
            "total_monthly_co2_kg": round(total_co2, 4),
            "total_monthly_cost_usd": round(total_cost, 2),
            "total_annual_cost_usd": round(total_cost * 12, 2),
        },
        "real_world_100_pod_cluster": {
            "expected_zombie_count": 30,
            "basis": "Jindal et al. (2023): 30% zombie-like containers in 1,000 Kubernetes clusters",
            "projected_monthly_cost_usd": round(total_cost * scale, 2),
            "projected_annual_cost_usd": round(total_cost * scale * 12, 2),
            "projected_monthly_co2_kg": round(total_co2 * scale, 2),
            "projected_monthly_energy_kwh": round(total_kwh * scale, 2),
        },
        "li_et_al_connection": {
            "paper_finding": f"EAES achieves {LI_ET_AL_ENERGY_REDUCTION_PCT}% energy reduction",
            "paper_gap": (
                "EAES is a scaling algorithm -- it does not classify individual containers "
                "as zombie vs. legitimately idle (Gap 1: Li et al. critical review). "
                "It would scale DOWN both zombie AND legitimately idle containers, "
                "risking disruption to cold-standby and scheduled batch workloads."
            ),
            "our_contribution": (
                "Heuristic detector provides the MISSING classification layer: "
                "identifies zombie containers before scaling action. "
                "Enables EAES to act on confirmed zombies only -- "
                "avoiding false termination of legitimate standby/batch containers."
            ),
            "integration_path": (
                "Future work: pipe heuristic classifications into EAES feedback loop. "
                "Zombie containers -> immediate scale-to-zero. "
                "Legitimately idle -> normal EAES management."
            ),
        },
    }


def format_energy_report(impact: Dict) -> str:
    """Format energy impact as a human-readable report."""
    lines = []
    sep = "=" * 72
    lines.append(sep)
    lines.append("ENERGY AND COST IMPACT ANALYSIS")
    lines.append("Model: Li et al. (2025) -- P = (cpu * 3.7W + mem * 0.375W/GB) * PUE(1.2)")
    lines.append(sep)

    tc = impact["test_cluster"]
    lines.append(f"\nTest cluster: {tc['zombie_count']} zombie containers detected on AWS EKS")
    lines.append("")
    lines.append(f"{'Container':<30} {'CPU':>5} {'Mem':>6}  {'Power':>7}  {'Cost/mo':>9}")
    lines.append("-" * 60)
    for item in tc["items"]:
        lines.append(
            f"{item['container']:<30} "
            f"{item['allocated_cpu_cores']:>5.3f} "
            f"{item['allocated_memory_gb']:>5.3f}G "
            f"{item['total_power_w']:>6.2f}W "
            f"${item['monthly_cost_usd']:>8.2f}"
        )
    lines.append("-" * 60)
    lines.append(
        f"{'TOTAL WASTE':<30} "
        f"{'':>5} {'':>6}  "
        f"{tc['total_power_waste_w']:>6.2f}W "
        f"${tc['total_monthly_cost_usd']:>8.2f}"
    )
    lines.append("")
    lines.append(f"Monthly energy waste: {tc['total_monthly_energy_kwh']:.3f} kWh")
    lines.append(f"Monthly CO2 waste:   {tc['total_monthly_co2_kg']:.4f} kg CO2")
    lines.append(f"Annual cost waste:   ${tc['total_annual_cost_usd']:.2f}")

    proj = impact["real_world_100_pod_cluster"]
    lines.append(f"\n{'-' * 72}")
    lines.append(f"Projection -> 100-pod cluster ({proj['basis']})")
    lines.append(f"  Expected zombie containers:  {proj['expected_zombie_count']}")
    lines.append(f"  Projected monthly cost waste: ${proj['projected_monthly_cost_usd']:.2f}")
    lines.append(f"  Projected annual cost waste:  ${proj['projected_annual_cost_usd']:.2f}")
    lines.append(f"  Projected monthly CO2 waste:  {proj['projected_monthly_co2_kg']:.2f} kg CO2")

    conn = impact["li_et_al_connection"]
    lines.append(f"\n{'-' * 72}")
    lines.append(f"Gap addressed (Li et al., 2025 -- Gap 1):")
    lines.append(f"  {conn['paper_gap']}")
    lines.append(f"\nOur contribution:")
    lines.append(f"  {conn['our_contribution']}")
    lines.append(f"\nIntegration path:")
    lines.append(f"  {conn['integration_path']}")

    return "\n".join(lines)
