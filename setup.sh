#!/bin/bash
# =============================================================================
# Setup script for Zombie Container Detection System on AWS EKS
# =============================================================================
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-zombie-detector-cluster}"
REGION="${AWS_REGION:-us-east-1}"
NODE_TYPE="${NODE_TYPE:-t3.medium}"
NODE_COUNT="${NODE_COUNT:-2}"

echo "============================================"
echo "Zombie Container Detection - AWS EKS Setup"
echo "============================================"
echo "Cluster: $CLUSTER_NAME"
echo "Region:  $REGION"
echo "Nodes:   $NODE_COUNT x $NODE_TYPE"
echo ""

# Check prerequisites
command -v aws >/dev/null 2>&1 || { echo "ERROR: aws CLI not found. Install: https://aws.amazon.com/cli/"; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/"; exit 1; }
command -v eksctl >/dev/null 2>&1 || { echo "ERROR: eksctl not found. Install: https://eksctl.io/installation/"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found."; exit 1; }

# Step 1: Create EKS cluster
echo ""
echo "[1/7] Creating EKS cluster (this takes 10-15 minutes)..."
if eksctl get cluster --name "$CLUSTER_NAME" --region "$REGION" 2>/dev/null; then
    echo "Cluster '$CLUSTER_NAME' already exists, skipping creation."
else
    eksctl create cluster \
        --name "$CLUSTER_NAME" \
        --region "$REGION" \
        --node-type "$NODE_TYPE" \
        --nodes "$NODE_COUNT" \
        --nodes-min 1 \
        --nodes-max 3 \
        --managed
fi

# Update kubeconfig
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"
echo "Cluster ready. Nodes:"
kubectl get nodes

# Step 2: Create namespaces
echo ""
echo "[2/7] Creating namespaces..."
kubectl apply -f kubernetes/namespaces.yaml

# Step 3: Deploy RBAC
echo ""
echo "[3/7] Deploying RBAC..."
kubectl apply -f kubernetes/rbac.yaml

# Step 4: Deploy Prometheus
echo ""
echo "[4/7] Deploying Prometheus monitoring..."
kubectl apply -f kubernetes/prometheus/config.yaml
kubectl apply -f kubernetes/prometheus/deployment.yaml

echo "Waiting for Prometheus to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/prometheus-server -n monitoring

# Step 5: Deploy Grafana
echo ""
echo "[5/7] Deploying Grafana with auto-provisioned dashboard..."
kubectl apply -f kubernetes/grafana/datasource.yaml
kubectl apply -f kubernetes/grafana/dashboard-provider.yaml
kubectl apply -f kubernetes/grafana/dashboard.yaml
kubectl apply -f kubernetes/grafana/deployment.yaml

echo "Waiting for Grafana to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/grafana -n monitoring

# Step 6: Deploy test scenarios
echo ""
echo "[6/7] Deploying test scenarios..."
for f in kubernetes/test-scenarios/*.yaml; do
    kubectl apply -f "$f"
done

echo "Waiting for test pods to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment --all -n test-scenarios

echo ""
echo "Test scenario pods:"
kubectl get pods -n test-scenarios -o wide

# Step 7: Build and deploy detector
echo ""
echo "[7/7] Building and deploying zombie detector..."

# Build Docker image
docker build -t zombie-detector:latest .

# For EKS, we need to push to ECR
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/zombie-detector"

# Create ECR repo if it doesn't exist
aws ecr describe-repositories --repository-names zombie-detector --region "$REGION" 2>/dev/null || \
    aws ecr create-repository --repository-name zombie-detector --region "$REGION"

# Login to ECR
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# Tag and push
docker tag zombie-detector:latest "$ECR_REPO:latest"
docker push "$ECR_REPO:latest"

# Update deployment image to ECR
sed "s|image: zombie-detector:latest|image: $ECR_REPO:latest|" kubernetes/detector/deployment.yaml | kubectl apply -f -

echo ""
echo "============================================"
echo "SETUP COMPLETE"
echo "============================================"
echo ""
echo "Test scenarios are running. Wait at least 30-60 minutes for"
echo "metrics to accumulate before running the detector or evaluation."
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n test-scenarios      # Check test pods"
echo "  kubectl get pods -n monitoring           # Check Prometheus & Grafana"
echo "  kubectl get pods -n zombie-detector      # Check detector"
echo "  kubectl logs -n zombie-detector deployment/zombie-detector  # View detection results"
echo ""
echo "To access Grafana dashboard:"
echo "  kubectl port-forward -n monitoring svc/grafana 3000:3000"
echo "  Open http://localhost:3000 (admin/admin)"
echo ""
echo "To port-forward Prometheus:"
echo "  kubectl port-forward -n monitoring svc/prometheus-server 9090:9090"
echo ""
echo "To run evaluation locally:"
echo "  kubectl port-forward -n monitoring svc/prometheus-server 9090:9090 &"
echo "  python -m src.evaluation --prometheus-url=http://localhost:9090"
echo ""
