#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_NS="llm-sandbox-demo"
RUNC_NS="runc-warmpool"
KATA_NS="kata-warmpool"
VICTIM_NS="victim"
WEBUI_NS="web-ui"

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
    info "Waiting for SandboxWarmPool/$name in $ns ($desired ready replicas) ..."
    for i in $(seq 1 $(( timeout_secs / 5 ))); do
        ready=$(oc get sandboxwarmpool "$name" -n "$ns" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        if [ "${ready:-0}" -ge "${desired:-0}" ] && [ "${desired:-0}" -gt 0 ]; then
            info "Sandbox warm pool $ns/$name is ready ($ready/$desired replicas)"
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

echo ""
info "This script deploys the LLM Agent Sandbox Demo manually."
info "For workshop/RHDP deployments, use the Helm chart in bootstrap/ via ArgoCD instead."
echo ""

# ── Step 1: Create Namespaces ─────────────────────────────────────
info "=== Step 1: Creating Namespaces ==="

for ns in "$BACKEND_NS" "$RUNC_NS" "$KATA_NS" "$VICTIM_NS" "$WEBUI_NS"; do
    oc create namespace "$ns" --dry-run=client -o yaml | oc apply -f -
done

# ── Step 2: LLM Credentials ──────────────────────────────────────
info "=== Step 2: Setting Up LLM Credentials ==="

if [ -n "${LLM_API_KEY:-}" ]; then
    info "LLM_API_KEY is set, will be used by agent-backend"
elif [ -n "${GCP_CREDENTIALS_FILE:-}" ] || [ -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
    GCP_CREDS="${GCP_CREDENTIALS_FILE:-$HOME/.config/gcloud/application_default_credentials.json}"
    if ! oc get secret gcp-credentials -n "$BACKEND_NS" &>/dev/null; then
        info "Creating GCP credentials secret from $GCP_CREDS ..."
        oc create secret generic gcp-credentials \
            --from-file=application_default_credentials.json="$GCP_CREDS" \
            -n "$BACKEND_NS"
    else
        info "GCP credentials secret already exists"
    fi
else
    warn "No LLM_API_KEY or GCP credentials found."
    warn "Set LLM_API_KEY env var, or run 'gcloud auth application-default login'."
fi

# ── Step 3: Deploy Sandbox Resources ─────────────────────────────
info "=== Step 3: Setting Up Sandbox Warm Pools ==="
info "(Requires: Agent Sandbox operator and kata RuntimeClass)"

oc apply -f "$SCRIPT_DIR/02-sandbox/sandbox-template.yaml"
oc apply -f "$SCRIPT_DIR/02-sandbox/warm-pool.yaml"

info "Sandbox resources applied to $RUNC_NS."
info "For kata warm pool in $KATA_NS, deploy OSC first (see 01-operators/)."

# ── Step 4: Deploy Security Demo (Victim Pod + SCC) ──────────────
info "=== Step 4: Deploying Security Demo Resources ==="

oc apply -f "$SCRIPT_DIR/06-security-demo/victim-pod/deployment.yaml"
oc apply -f "$SCRIPT_DIR/06-security-demo/rbac/scc-and-sa.yaml"
wait_for_resource deployment payment-service "$VICTIM_NS"

# ── Step 5: Deploy Agent Backend ─────────────────────────────────
info "=== Step 5: Deploying Agent Backend ==="

oc apply -f "$SCRIPT_DIR/04-agent-backend/deployment.yaml"
wait_for_resource deployment agent-backend "$BACKEND_NS"

# ── Step 6: Deploy Chat UI ───────────────────────────────────────
info "=== Step 6: Deploying Chat UI ==="

oc create configmap chat-ui-files \
    --from-file=index.html="$SCRIPT_DIR/05-chat-ui/index.html" \
    --from-file=nginx.conf="$SCRIPT_DIR/05-chat-ui/nginx.conf" \
    -n "$WEBUI_NS" --dry-run=client -o yaml | oc apply -f -

oc apply -f "$SCRIPT_DIR/05-chat-ui/deployment.yaml"
wait_for_resource deployment chat-ui "$WEBUI_NS"

# ── Step 7: Wait for Warm Pool ───────────────────────────────────
info "=== Step 7: Waiting for Warm Pool ==="

wait_for_warm_pool code-sandbox-pool "$RUNC_NS"

# ── Done ──────────────────────────────────────────────────────────
info "=== Deployment Complete ==="

ROUTE=$(oc get route chat-ui -n "$WEBUI_NS" -o jsonpath='{.spec.host}' 2>/dev/null || echo "pending")
echo ""
info "Chat UI:        https://$ROUTE"
info "Agent Backend:  http://agent-backend.$BACKEND_NS.svc:8000"
info "Sandboxes:      oc get sandboxes -n $RUNC_NS"
echo ""
info "Next steps:"
info "  1. Open https://$ROUTE in your browser"
info "  2. Ask the assistant to write code, then click Run to execute it"
info "  3. For the security demo, see 06-security-demo/README.md"
info "  4. To switch to kata: ./06-security-demo/switch-to-kata.sh"
