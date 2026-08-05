#!/bin/bash
set -e

BACKEND_NS="${1:-llm-sandbox-demo}"
KATA_NS="${2:-kata-warmpool}"

echo "=== Switching agent-backend to kata-remote sandbox ==="
echo ""
echo "Patching agent-backend deployment to use namespace '$KATA_NS'..."

oc set env deployment/agent-backend \
  -n "$BACKEND_NS" \
  SANDBOX_NAMESPACE="$KATA_NS"

echo ""
echo "Waiting for rollout..."
oc rollout status deployment/agent-backend -n "$BACKEND_NS" --timeout=120s

echo ""
echo "=== Done ==="
echo "The agent-backend now creates sandboxes in '$KATA_NS'"
echo "which uses kata-remote runtime with VM isolation."
