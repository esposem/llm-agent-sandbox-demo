# LLM Agent Sandbox Demo

An end-to-end demo on OpenShift that lets you chat with an LLM through a custom chat UI. When you ask it to write code, you can execute it in isolated sandbox pods via the [Agent Sandbox Operator](https://github.com/openshift/kubernetes-sigs-agent-sandbox). A built-in security demo shows why Kata Containers matter: the same attack code steals credentials with runc but finds nothing with `kata` VM isolation.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Chat UI   │────▶│  Agent Backend   │────▶│  LLM (LiteMaaS   │
│  (Browser)  │◀────│  (FastAPI proxy) │◀────│  or Vertex AI)   │
└──────┬──────┘     └───────┬──────────┘     └──────────────────┘
       │                    │
  click "Run"          execute code
       │                    │
       └───────────▶┌───────▼──────────┐
                    │  Sandbox Claim   │
                    │  (runc or kata)  │
                    │                  │
                    │  ┌────────────┐  │
                    │  │ Python pod │  │  ◀── from Warm Pool (2 ready)
                    │  └────────────┘  │
                    └──────────────────┘

Namespaces:
  llm-sandbox-demo   ─ agent-backend Deployment + ServiceAccount
  web-ui             ─ chat UI (nginx) + Route
  runc-warmpool      ─ SandboxTemplate (runc, hostPID) + WarmPool
  kata-warmpool      ─ SandboxTemplate (kata, hostPID) + WarmPool
  victim             ─ payment-service with fake credentials (security demo)
```

**Flow:**
1. User chats in the Chat UI
2. Messages stream to the Agent Backend (OpenAI-compatible API proxy)
3. Agent Backend calls the LLM (via LiteMaaS, Vertex AI, or any OpenAI-compatible endpoint)
4. The LLM responds with code in fenced markdown blocks
5. The Chat UI renders code blocks with syntax highlighting and a **Run** button
6. Clicking Run sends the code to `/v1/sandbox/execute`, which:
   - Claims a pre-warmed sandbox pod from the warm pool in `SANDBOX_NAMESPACE`
   - Auto-installs any missing Python packages
   - Writes and runs the code inside the sandbox
   - Returns stdout/stderr back to the UI

## Components

| Component | Description |
|-----------|-------------|
| **Agent Backend** | Python FastAPI service that proxies OpenAI-format chat requests to the LLM and handles sandbox code execution |
| **Agent Sandbox Operator** | Creates and manages isolated sandbox pods (installed by the Helm chart) |
| **Warm Pools** | 2 pre-warmed pods in each namespace (`runc-warmpool` and `kata-warmpool`) ready for instant code execution |
| **Chat UI** | Lightweight single-page chat app (nginx + HTML) with streaming, syntax highlighting, and Run buttons |
| **OSC (OpenShift Sandboxed Containers)** | Provides the `kata` RuntimeClass for VM-isolated sandboxes (installed by Ansible workload) |

## Deployment Modes

### Workshop / RHDP (Helm chart via ArgoCD)

For the agent-sandbox workshop on RHDP, everything is deployed automatically:

1. **AgnosticV** provisions an ARO cluster and runs infra workloads:
   - OpenShift GitOps (ArgoCD)
   - LiteMaaS (LLM access)
   - `ocp4_workload_osc_configure_aro` (installs OSC, configures peer pods, creates `kata` RuntimeClass)
   - `ocp4_workload_gitops_bootstrap` (creates ArgoCD Application pointing to `bootstrap/`)
   - Showroom (workshop instructions)

2. **ArgoCD** syncs the **Helm chart** in `bootstrap/`, which deploys:
   - Agent Sandbox operator (Namespace + OperatorGroup + Subscription)
   - Namespaces: `llm-sandbox-demo`, `web-ui`, `runc-warmpool`, `kata-warmpool`, `victim`
   - Agent backend with `SANDBOX_NAMESPACE=runc-warmpool`
   - Chat UI with Route
   - SandboxTemplates and WarmPools in both runc and kata namespaces
   - Victim payment-service pod (security demo)
   - SCC and RBAC for the hostPID security demo

Helm values are injected by agnosticv via `ocp4_workload_gitops_bootstrap_helm_values`.

### Manual (deploy.sh)

For local development or standalone clusters:

```bash
./deploy.sh
```

Prerequisites:
- OpenShift 4.14+ cluster
- `oc` CLI logged in as cluster-admin
- Agent Sandbox operator already installed
- For kata demo: OSC operator installed with `kata` RuntimeClass (see `01-operators/`)
- LLM endpoint (set `LLM_API_KEY` env var, or GCP credentials for Vertex AI)

## Helm Chart (bootstrap/)

The `bootstrap/` directory contains a Helm chart deployed by ArgoCD:

```
bootstrap/
├── Chart.yaml
├── values.yaml              # defaults (overridden by agnosticv)
├── files/
│   └── index.html           # chat UI page
└── templates/
    ├── namespaces.yaml       # llm-sandbox-demo, web-ui
    ├── agent-sandbox-operator.yaml  # operator install (sync-wave 0-1)
    ├── agent-backend.yaml    # SA, Deployment, Service
    ├── chat-ui.yaml          # ConfigMap, Deployment, Service, Route
    └── sandbox.yaml          # warm pool namespaces, victim pod, SCC,
                              # SandboxTemplates, WarmPools, RBAC
```

Key values:

```yaml
backend:
  namespace: llm-sandbox-demo
  image: quay.io/eesposit/agent-backend:latest
  warmPoolName: code-sandbox-pool

llm:
  model: granite-3-2-8b-instruct
  apiUrl: ""       # LiteMaaS endpoint (injected by agnosticv)
  apiKey: ""       # LiteMaaS key (injected by agnosticv)

sandbox:
  runcNamespace: runc-warmpool
  kataNamespace: kata-warmpool

agentSandbox:
  channel: preview-0.9
  sandboxImage: quay.io/eesposit/python-runtime-sandbox:latest
  warmPoolReplicas: 2
```

## Switching Between Runtimes

The agent-backend reads `SANDBOX_NAMESPACE` to decide which warm pool to claim sandboxes from. Both pools are pre-built and ready at deploy time.

```bash
# Switch to kata (VM isolation)
./06-security-demo/switch-to-kata.sh

# Switch back to runc
./06-security-demo/switch-to-runc.sh
```

These scripts patch the `SANDBOX_NAMESPACE` env var on the agent-backend Deployment and wait for the rollout. ArgoCD self-heal is disabled, so the env var change persists.

## Security Demo

The security demo is deployed as part of the Helm chart (no extra steps needed). It includes:

- A **victim** `payment-service` pod in the `victim` namespace with fake credentials in env vars
- **hostPID + privileged** SCC bound to sandbox service accounts in both warm pool namespaces
- SandboxTemplates with `hostPID: true` and pod affinity to land on the same node as the victim

**Workshop flow:**
1. Run normal code (Fibonacci, data analysis) — sandboxes work with runc
2. Run attack code — read `/proc/<pid>/environ` to steal the victim's credentials (works with runc)
3. Switch to kata: `./06-security-demo/switch-to-kata.sh`
4. Re-run the same attack — kata VM isolation blocks it, `/proc` only shows VM processes

See [`06-security-demo/README.md`](06-security-demo/README.md) for attack payloads and expected output.

## Configuration

### Changing the Model

In workshop mode, the model is configured via agnosticv (LiteMaaS). For manual deployment, edit `04-agent-backend/deployment.yaml`:

```yaml
env:
  - name: MODEL_NAME
    value: "granite-3-2-8b-instruct"
  - name: LLM_API_URL
    value: "https://your-endpoint/v1"
  - name: LLM_API_KEY
    value: "your-key"
```

### Warm Pool Size

In the Helm chart, set `agentSandbox.warmPoolReplicas` in `bootstrap/values.yaml`. For manual deployment, edit `02-sandbox/warm-pool.yaml`.

## Troubleshooting

```bash
# Check pods across all demo namespaces
oc get pods -n llm-sandbox-demo
oc get pods -n runc-warmpool
oc get pods -n kata-warmpool
oc get pods -n web-ui
oc get pods -n victim

# Check agent backend logs
oc logs -n llm-sandbox-demo -l app=agent-backend

# Check warm pool status
oc get sandboxwarmpools -n runc-warmpool
oc get sandboxwarmpools -n kata-warmpool
oc get sandboxes -n runc-warmpool

# Check which namespace the agent-backend targets
oc get deployment agent-backend -n llm-sandbox-demo -o jsonpath='{.spec.template.spec.containers[0].env}' | python3 -m json.tool

# Check ArgoCD sync status (workshop mode)
oc get application -n openshift-gitops
```

## Directory Layout

```
llm-agent-sandbox-demo/
├── deploy.sh                    # Manual deployment script
├── bootstrap/                   # Helm chart (deployed by ArgoCD in workshop mode)
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── files/index.html
│   └── templates/
├── 01-operators/                # Operator install scripts (reference / manual use)
│   ├── agent-sandbox/
│   └── openshift-sandboxed-containers/
├── 02-sandbox/                  # Standalone sandbox template + warm pool YAMLs
├── 03-sandbox-app/              # Sandbox container image (Dockerfile + Python)
├── 04-agent-backend/            # Agent backend (FastAPI app + Dockerfile)
├── 05-chat-ui/                  # Chat UI (HTML + nginx config)
└── 06-security-demo/            # Security demo (switch scripts, attack payloads)
    ├── switch-to-kata.sh
    ├── switch-to-runc.sh
    ├── victim-pod/
    ├── rbac/
    ├── sandbox-templates/
    └── attack-payloads/
```
