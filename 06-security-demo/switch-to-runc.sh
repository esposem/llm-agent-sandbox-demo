#!/bin/bash
set -euo pipefail

BACKEND_NS="${1:-llm-sandbox-demo}"
RUNC_NS="${2:-runc-warmpool}"
CM_NAME="sandbox-runtime"

echo "=== Switching agent-backend back to runc sandbox ==="
echo ""
echo "Writing ConfigMap/$CM_NAME SANDBOX_NAMESPACE=$RUNC_NS in $BACKEND_NS ..."

oc create configmap "$CM_NAME" \
  -n "$BACKEND_NS" \
  --from-literal=SANDBOX_NAMESPACE="$RUNC_NS" \
  --dry-run=client -o yaml | oc apply -f -

echo ""
echo "Recycling agent-backend pod..."
oc delete pod -n "$BACKEND_NS" -l app=agent-backend --wait=true --timeout=60s
oc rollout status deployment/agent-backend -n "$BACKEND_NS" --timeout=120s

CURRENT=$(oc exec -n "$BACKEND_NS" deploy/agent-backend -- printenv SANDBOX_NAMESPACE 2>/dev/null || true)
if [ "$CURRENT" != "$RUNC_NS" ]; then
  echo "ERROR: SANDBOX_NAMESPACE inside the pod is '${CURRENT:-<unset>}', expected '$RUNC_NS'." >&2
  echo "agent-backend must take SANDBOX_NAMESPACE from ConfigMap/$CM_NAME (optional configMapKeyRef)." >&2
  exit 1
fi

echo ""
echo "=== Done ==="
echo "The agent-backend now creates sandboxes in '$RUNC_NS' (runc runtime)."
