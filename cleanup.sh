#!/bin/bash
# =============================================================================
# Cleanup: remove all deployed resources
# =============================================================================
set -euo pipefail

echo "Cleaning up Zombie Container Detection System..."

kubectl delete namespace test-scenarios --ignore-not-found
kubectl delete namespace zombie-detector --ignore-not-found
kubectl delete namespace monitoring --ignore-not-found
kubectl delete clusterrole zombie-detector prometheus --ignore-not-found
kubectl delete clusterrolebinding zombie-detector prometheus --ignore-not-found

echo "Cleanup complete."

# Optional: delete EKS cluster
if [ "${DELETE_CLUSTER:-false}" = "true" ]; then
    CLUSTER_NAME="${CLUSTER_NAME:-zombie-detector-cluster}"
    REGION="${AWS_REGION:-us-east-1}"
    echo "Deleting EKS cluster '$CLUSTER_NAME'..."
    eksctl delete cluster --name "$CLUSTER_NAME" --region "$REGION"
    echo "Cluster deleted."
fi
