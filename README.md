# LLM Agent Sandbox Demo

An end-to-end demo on OpenShift that lets you chat with Claude through a custom chat UI. When you ask it to write code, you can execute it in isolated sandbox pods running on the `kata-remote` runtime via the [Agent Sandbox Operator](https://github.com/openshift/kubernetes-sigs-agent-sandbox).

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Chat UI   │────▶│  Agent Backend   │────▶│  Claude Sonnet 4 │
│  (Browser)  │◀────│  (FastAPI proxy) │◀────│  (Vertex AI)     │
└──────┬──────┘     └───────┬──────────┘     └──────────────────┘
       │                    │
  click "Run"          execute code
       │                    │
       └───────────▶┌───────▼──────────┐
                    │  Sandbox Claim   │
                    │  (kata-remote)   │
                    │                  │
                    │  ┌────────────┐  │
                    │  │ Python pod │  │  ◀── from Warm Pool (2 ready)
                    │  └────────────┘  │
                    └──────────────────┘
```

**Flow:**
1. User chats in the Chat UI
2. Messages stream to the Agent Backend (OpenAI-compatible API proxy)
3. Agent Backend converts messages to the Anthropic Messages API and calls Claude via Vertex AI `rawPredict`
4. Claude responds with code in fenced markdown blocks
5. The Chat UI renders code blocks with syntax highlighting and a **Run** button
6. Clicking Run sends the code to `/v1/sandbox/execute`, which:
   - Claims a pre-warmed sandbox pod from the warm pool
   - Auto-installs any missing Python packages
   - Writes and runs the code inside the sandbox
   - Returns stdout/stderr back to the UI

## Components

| Component | Description |
|-----------|-------------|
| **Claude Sonnet 4** | Anthropic's model accessed via Google Vertex AI `rawPredict` endpoint |
| **Agent Backend** | Python FastAPI service that proxies OpenAI-format chat requests to Anthropic's API and handles sandbox code execution |
| **Agent Sandbox Operator** | Creates and manages isolated sandbox pods (pre-installed) |
| **Warm Pool** | 2 pre-warmed `kata-remote` pods ready for instant code execution |
| **Chat UI** | Lightweight single-page chat app (nginx + HTML) with streaming, syntax highlighting, and Run buttons on code blocks |

## Prerequisites

- OpenShift 4.14+ cluster (no GPU required)
- `oc` CLI logged into the cluster as cluster-admin
- Red Hat Build of Agent Sandbox Operator already installed
- `kata-remote` RuntimeClass available (via OpenShift Sandboxed Containers operator with peer pods)
- GCP project with Anthropic models enabled on Vertex AI
- GCP application default credentials (`gcloud auth application-default login`)

## Deploy

```bash
./deploy.sh
```

The script will:
1. Create the namespace and a Kubernetes secret from your GCP credentials
2. Create a SandboxTemplate (kata-remote) and WarmPool (2 replicas)
3. Deploy the Agent Backend (pre-built image from `quay.io/eesposit/agent-backend`)
4. Deploy the Chat UI with an OpenShift Route
5. Wait for the sandbox warm pool to be ready

### GCP Credentials

The deploy script looks for credentials at `$GCP_CREDENTIALS_FILE`, falling back to `~/.config/gcloud/application_default_credentials.json`. To set up:

```bash
gcloud auth application-default login
# or
export GCP_CREDENTIALS_FILE=/path/to/your/credentials.json
```

The credentials are stored as a Kubernetes secret (`gcp-credentials`) and mounted into the agent-backend pod. They are **not** stored in this repository (blocked by `.gitignore`).

## Manual Deployment

If you prefer to deploy step-by-step:

```bash
# 1. Namespace & GCP credentials
oc create namespace llm-sandbox-demo
oc create secret generic gcp-credentials \
  --from-file=application_default_credentials.json=$HOME/.config/gcloud/application_default_credentials.json \
  -n llm-sandbox-demo

# 2. Sandbox Warm Pool
oc apply -f 02-sandbox/sandbox-template.yaml
oc apply -f 02-sandbox/warm-pool.yaml

# 3. Agent Backend
oc apply -f 04-agent-backend/deployment.yaml

# 4. Chat UI
oc create configmap chat-ui-files \
  --from-file=index.html=05-chat-ui/index.html \
  --from-file=nginx.conf=05-chat-ui/nginx.conf \
  -n llm-sandbox-demo --dry-run=client -o yaml | oc apply -f -
oc apply -f 05-chat-ui/deployment.yaml
```

## Configuration

### Changing the Model

Edit `04-agent-backend/deployment.yaml` and update the environment variables:

```yaml
env:
  - name: MODEL_NAME
    value: "claude-sonnet-4@20250514"  # change to another Anthropic model
  - name: VERTEX_REGION
    value: "us-east5"                   # must support rawPredict
  - name: VERTEX_PROJECT
    value: "your-gcp-project"
```

### Warm Pool Size

Edit `02-sandbox/warm-pool.yaml`:

```yaml
spec:
  replicas: 5  # increase for higher concurrency
```

### Sandbox Resources

Edit `02-sandbox/sandbox-template.yaml` to adjust CPU, memory, installed packages, or the container image.

## Usage

1. Open the URL printed at the end of `deploy.sh` (or run `oc get route chat-ui -n llm-sandbox-demo`)
2. Try prompts like:
   - "Write a Python script that calculates the first 20 Fibonacci numbers"
   - "Create a bash script that shows system information"
   - "Write a Python program that generates a random maze and solves it"
3. Click the **Run** button on any code block to execute it in a sandbox
4. View stdout/stderr output directly below the code block

## Security Demo: Kata vs Runc

The [`06-security-demo/`](06-security-demo/) directory contains a ready-to-run demonstration of why Kata Containers are essential for running untrusted code. It deploys a victim pod with credentials in its environment variables and lets you run AI-generated attack code in both runc and kata sandboxes.

**With runc** (shared kernel): the attack scans `/proc` and steals database passwords, Stripe keys, and AWS credentials from other pods on the same node.

**With kata** (VM isolation): the same code, same pod spec, same privileges — but `/proc` only shows VM-internal processes. The attack finds nothing.

```bash
# Deploy victim pod, RBAC, and sandbox templates
oc apply -f 06-security-demo/victim-pod/deployment.yaml
oc apply -f 06-security-demo/rbac/scc-and-sa.yaml
oc apply -f 06-security-demo/sandbox-templates/

# Switch between runtimes
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-runc-hostpid  # vulnerable
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-kata-hostpid  # protected
```

See [`06-security-demo/README.md`](06-security-demo/README.md) for the full walkthrough, attack payloads, and expected output.

## Troubleshooting

```bash
# Check all pods
oc get pods -n llm-sandbox-demo

# Check agent backend logs
oc logs -n llm-sandbox-demo -l app=agent-backend

# Check warm pool status
oc get sandboxwarmpools -n llm-sandbox-demo
oc get sandboxes -n llm-sandbox-demo

# Check chat UI logs
oc logs -n llm-sandbox-demo -l app=chat-ui

# Verify GCP credentials secret exists
oc get secret gcp-credentials -n llm-sandbox-demo
```
