#!/bin/bash
set -e

BACKEND_NS="${1:-llm-sandbox-demo}"
RUNC_NS="${2:-runc-warmpool}"

echo "=== Switching agent-backend back to runc sandbox ==="
echo ""
echo "Patching agent-backend deployment to use namespace '$RUNC_NS'..."

oc set env deployment/agent-backend \
  -n "$BACKEND_NS" \
  SANDBOX_NAMESPACE="$RUNC_NS"

echo ""
echo "Waiting for rollout..."
oc rollout status deployment/agent-backend -n "$BACKEND_NS" --timeout=120s

echo ""
echo "=== Done ==="
echo "The agent-backend now creates sandboxes in '$RUNC_NS' (runc runtime)."
