#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="llm-sandbox-demo"
VICTIM_NAMESPACE="victim"
WEBUI_NAMESPACE="web-ui"

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[1;31m[ERROR]\033[0m $*"; exit 1; }

wait_for_resource() {
    local kind="$1" name="$2" ns="$3" condition="${4:-condition=Available}" timeout="${5:-300s}"
    info "Waiting for $kind/$name ($condition) ..."
    oc wait "$kind/$name" -n "$ns" --for="$condition" --timeout="$timeout" 2>/dev/null || true
}

wait_for_warm_pool() {
    local name="$1" ns="$2" timeout_secs="${3:-600}"
    local desired ready
    desired=$(oc get sandboxwarmpool "$name" -n "$ns" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
    info "Waiting for SandboxWarmPool/$name ($desired ready replicas) ..."
    for i in $(seq 1 $(( timeout_secs / 5 ))); do
        ready=$(oc get sandboxwarmpool "$name" -n "$ns" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        if [ "${ready:-0}" -ge "${desired:-0}" ] && [ "${desired:-0}" -gt 0 ]; then
            info "Sandbox warm pool is ready ($ready/$desired replicas)"
            return 0
        fi
        [ "$i" -eq $(( timeout_secs / 5 )) ] && warn "Timed out waiting for warm pool - check: oc get sandboxes -n $ns"
        sleep 5
    done
}

# ── Step 0: Preflight ──────────────────────────────────────────────
info "Checking prerequisites ..."
command -v oc   >/dev/null || error "'oc' CLI not found"
oc whoami       >/dev/null || error "Not logged in to an OpenShift cluster"

# ── Step 1: Create Namespace & GCP Credentials ────────────────────
info "=== Step 1: Setting Up Namespace and Credentials ==="

oc create namespace "$NAMESPACE" --dry-run=client -o yaml | oc apply -f -
oc create namespace "$VICTIM_NAMESPACE" --dry-run=client -o yaml | oc apply -f -

if ! oc get secret gcp-credentials -n "$NAMESPACE" &>/dev/null; then
    GCP_CREDS="${GCP_CREDENTIALS_FILE:-$HOME/.config/gcloud/application_default_credentials.json}"
    if [ ! -f "$GCP_CREDS" ]; then
        error "GCP credentials not found at $GCP_CREDS. Set GCP_CREDENTIALS_FILE or run 'gcloud auth application-default login'."
    fi
    info "Creating GCP credentials secret from $GCP_CREDS ..."
    oc create secret generic gcp-credentials \
        --from-file=application_default_credentials.json="$GCP_CREDS" \
        -n "$NAMESPACE"
else
    info "GCP credentials secret already exists"
fi

# ── Step 2: Set Up Sandbox Warm Pool ──────────────────────────────
info "=== Step 2: Setting Up Sandbox Warm Pool ==="
info "(Assumes agent-sandbox and openshift-sandboxed-containers operators are already installed)"

oc apply -f "$SCRIPT_DIR/02-sandbox/sandbox-template.yaml"
oc apply -f "$SCRIPT_DIR/02-sandbox/warm-pool.yaml"

info "Sandbox warm pool resources applied."

# ── Step 3: Deploy Agent Backend ──────────────────────────────────
info "=== Step 3: Deploying Agent Backend ==="

oc apply -f "$SCRIPT_DIR/04-agent-backend/deployment.yaml"
wait_for_resource deployment agent-backend "$NAMESPACE"

# ── Step 4: Deploy Chat UI ────────────────────────────────────────
info "=== Step 4: Deploying Chat UI ==="

oc create namespace "$WEBUI_NAMESPACE" --dry-run=client -o yaml | oc apply -f -

oc create configmap chat-ui-files \
    --from-file=index.html="$SCRIPT_DIR/05-chat-ui/index.html" \
    --from-file=nginx.conf="$SCRIPT_DIR/05-chat-ui/nginx.conf" \
    -n "$WEBUI_NAMESPACE" --dry-run=client -o yaml | oc apply -f -

oc apply -f "$SCRIPT_DIR/05-chat-ui/deployment.yaml"
wait_for_resource deployment chat-ui "$WEBUI_NAMESPACE"

# ── Step 5: Wait for Warm Pool ────────────────────────────────────
info "=== Step 5: Waiting for Warm Pool ==="

wait_for_warm_pool code-sandbox-pool "$NAMESPACE"

# ── Done ──────────────────────────────────────────────────────────
info "=== Deployment Complete ==="

ROUTE=$(oc get route chat-ui -n "$WEBUI_NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "pending")
echo ""
info "Chat UI:        https://$ROUTE"
info "Agent Backend:  http://agent-backend.$NAMESPACE.svc:8000"
echo ""
info "Next steps:"
info "  1. Open https://$ROUTE in your browser"
info "  2. Ask the assistant to write code"
info "  3. Click the Run button on code blocks to execute in sandboxes!"
