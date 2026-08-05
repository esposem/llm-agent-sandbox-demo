# Agent Backend

FastAPI backend that orchestrates LLM-driven code execution via Kubernetes sandboxes.

## Rebuild and Redeploy

Build the image, push it, and restart the pod:

```bash
podman build -t quay.io/eesposit/agent-backend:latest .
podman push quay.io/eesposit/agent-backend:latest
oc rollout restart deployment/agent-backend -n llm-sandbox-demo
```

Or as a one-liner:

```bash
podman build -t quay.io/eesposit/agent-backend:latest . && podman push quay.io/eesposit/agent-backend:latest && oc rollout restart deployment/agent-backend -n llm-sandbox-demo
```

Watch the rollout:

```bash
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

## Deploy from Scratch

```bash
oc apply -f deployment.yaml
```
