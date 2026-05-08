"""
Prometheus metrics collector for zombie container detection.
Queries Prometheus for CPU, memory, and network metrics using PromQL.
"""

import time
import logging
import requests
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects container metrics from Prometheus."""

    def __init__(self, prometheus_url: str):
        self.prometheus_url = prometheus_url.rstrip("/")
        self._verify_connection()

    def _verify_connection(self):
        try:
            r = requests.get(f"{self.prometheus_url}/api/v1/status/config", timeout=5)
            r.raise_for_status()
            logger.info("Connected to Prometheus at %s", self.prometheus_url)
        except Exception as e:
            logger.warning("Cannot reach Prometheus at %s: %s", self.prometheus_url, e)

    def query_instant(self, promql: str) -> list:
        """Execute an instant PromQL query."""
        try:
            r = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": promql, "time": time.time()},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data["status"] != "success":
                logger.error("Prometheus query failed: %s", data.get("error", "unknown"))
                return []
            return data["data"]["result"]
        except Exception as e:
            logger.error("Prometheus instant query error: %s", e)
            return []

    def query_range(self, promql: str, duration_minutes: int = 60, step: int = 15) -> list:
        """Execute a range PromQL query."""
        end = time.time()
        start = end - (duration_minutes * 60)
        try:
            r = requests.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params={
                    "query": promql,
                    "start": start,
                    "end": end,
                    "step": step,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data["status"] != "success":
                logger.error("Prometheus range query failed: %s", data.get("error", "unknown"))
                return []
            return data["data"]["result"]
        except Exception as e:
            logger.error("Prometheus range query error: %s", e)
            return []

    def get_running_containers(self, exclude_namespaces: list = None) -> list:
        """Get list of running containers from Prometheus metrics."""
        if exclude_namespaces is None:
            exclude_namespaces = ["kube-system", "kube-public", "kube-node-lease"]

        ns_filter = ",".join(f'"{ns}"' for ns in exclude_namespaces)
        query = (
            f'count by (namespace, pod, container) '
            f'(container_cpu_usage_seconds_total{{container!="POD", container!="", '
            f'namespace!~"{"$|".join(exclude_namespaces)}"}})'
        )
        results = self.query_instant(query)
        containers = []
        for r in results:
            metric = r["metric"]
            containers.append({
                "namespace": metric.get("namespace", ""),
                "pod": metric.get("pod", ""),
                "container": metric.get("container", ""),
            })
        logger.info("Found %d running containers", len(containers))
        return containers

    def get_container_metrics(self, namespace: str, pod: str, container: str,
                              duration_minutes: int = 60) -> dict:
        """Get all metrics for a specific container over a time window."""
        labels = f'namespace="{namespace}", pod="{pod}", container="{container}"'

        cpu_query = f'rate(container_cpu_usage_seconds_total{{{labels}}}[5m])'
        mem_query = f'container_memory_usage_bytes{{{labels}}}'
        net_rx_query = f'rate(container_network_receive_bytes_total{{namespace="{namespace}", pod="{pod}"}}[5m])'
        net_tx_query = f'rate(container_network_transmit_bytes_total{{namespace="{namespace}", pod="{pod}"}}[5m])'

        cpu_data = self.query_range(cpu_query, duration_minutes)
        mem_data = self.query_range(mem_query, duration_minutes)
        net_rx_data = self.query_range(net_rx_query, duration_minutes)
        net_tx_data = self.query_range(net_tx_query, duration_minutes)

        return {
            "cpu": self._to_series(cpu_data),
            "memory": self._to_series(mem_data),
            "network_rx": self._to_series(net_rx_data),
            "network_tx": self._to_series(net_tx_data),
        }

    def get_container_resource_limits(self, namespace: str, pod: str, container: str) -> dict:
        """Get resource limits for a container."""
        labels = f'namespace="{namespace}", pod="{pod}", container="{container}"'

        cpu_limit_results = self.query_instant(
            f'container_spec_cpu_quota{{{labels}}} / container_spec_cpu_period{{{labels}}}'
        )
        mem_limit_results = self.query_instant(
            f'container_spec_memory_limit_bytes{{{labels}}}'
        )

        cpu_limit = None
        mem_limit = None

        if cpu_limit_results:
            val = float(cpu_limit_results[0]["value"][1])
            if val > 0:
                cpu_limit = val

        if mem_limit_results:
            val = float(mem_limit_results[0]["value"][1])
            if val > 0 and val < 1e18:  # filter out "unlimited" values
                mem_limit = val

        return {"cpu_limit": cpu_limit, "memory_limit": mem_limit}

    def _to_series(self, prom_results: list) -> pd.Series:
        """Convert Prometheus range query results to a pandas Series."""
        if not prom_results:
            return pd.Series(dtype=float)

        # Take the first matching time series
        values = prom_results[0].get("values", [])
        if not values:
            return pd.Series(dtype=float)

        timestamps = [pd.Timestamp(v[0], unit="s") for v in values]
        data = [float(v[1]) for v in values]
        return pd.Series(data=data, index=timestamps)
