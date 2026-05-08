#!/bin/bash
# =============================================================================
# Deploy all components (assumes cluster already exists and kubectl configured)
# =============================================================================
set -euo pipefail

echo "Deploying Zombie Container Detection System..."

# Namespaces
echo "[1/5] Creating namespaces..."
kubectl apply -f kubernetes/namespaces.yaml

# RBAC
echo "[2/5] Deploying RBAC..."
kubectl apply -f kubernetes/rbac.yaml

# Prometheus
echo "[3/5] Deploying Prometheus..."
kubectl apply -f kubernetes/prometheus/config.yaml
kubectl apply -f kubernetes/prometheus/deployment.yaml
echo "Waiting for Prometheus..."
kubectl wait --for=condition=available --timeout=120s deployment/prometheus-server -n monitoring

# Grafana
echo "[4/5] Deploying Grafana with auto-provisioned dashboard..."
kubectl apply -f kubernetes/grafana/datasource.yaml
kubectl apply -f kubernetes/grafana/dashboard-provider.yaml
kubectl apply -f kubernetes/grafana/dashboard.yaml
kubectl apply -f kubernetes/grafana/deployment.yaml
echo "Waiting for Grafana..."
kubectl wait --for=condition=available --timeout=120s deployment/grafana -n monitoring

# Test scenarios
echo "[5/5] Deploying test scenarios..."
for f in kubernetes/test-scenarios/*.yaml; do
    kubectl apply -f "$f"
done
echo "Waiting for test pods..."
kubectl wait --for=condition=available --timeout=120s deployment --all -n test-scenarios

echo ""
echo "All components deployed. Current pods:"
echo ""
echo "=== Monitoring ==="
kubectl get pods -n monitoring
echo ""
echo "=== Test Scenarios ==="
kubectl get pods -n test-scenarios
echo ""
echo "IMPORTANT: Wait 30-60 minutes for metrics to accumulate before running evaluation."
echo ""
echo "To access Grafana:"
echo "  kubectl port-forward -n monitoring svc/grafana 3000:3000 &"
echo "  Open http://localhost:3000 (admin/admin)"
echo ""
echo "To run detector locally:"
echo "  kubectl port-forward -n monitoring svc/prometheus-server 9090:9090 &"
echo "  python -m src.main --prometheus-url=http://localhost:9090 --details"
echo ""
echo "To run evaluation:"
echo "  python -m src.evaluation --prometheus-url=http://localhost:9090"
