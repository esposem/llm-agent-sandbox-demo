# Security Demo: Kata Containers vs Runc

This directory contains materials for demonstrating how Kata Containers protect against Kubernetes secret theft via AI-generated code.

## Overview

This demo shows:
- **With runc (vulnerable)**: AI-generated code can steal Kubernetes secrets from the namespace
- **With Kata (protected)**: VM isolation blocks the same attack

The agent backend selects sandboxes via the `WARMPOOL_NAME` env var (default: `code-sandbox-pool` in `04-agent-backend/deployment.yaml`). After step 3, point it at the demo warm pools you created.

## Quick Start

### 1. Deploy Victim Infrastructure

Create realistic secrets that can be stolen:

```bash
oc apply -f victim-infrastructure/secrets.yaml
```

This creates:
- `database-credentials` — DB passwords, Redis passwords, API keys
- `stripe-api-credentials` — Payment processing keys
- `app-config` ConfigMap — Admin tokens and internal API keys

### 2. Deploy RBAC Configuration

Create a service account with secret read permissions:

```bash
oc apply -f rbac/service-account.yaml
```

This creates:
- Service Account: `sandbox-demo-sa`
- Role: `secret-reader` (can read secrets, configmaps, pods)
- RoleBinding: Grants permissions to the service account

**Why**: Simulates a common configuration where apps need to read secrets.

### 3. Deploy Sandbox Templates

#### For Protected Demo (Kata):

```bash
oc apply -f sandbox-templates/kata-with-sa.yaml
oc apply -f sandbox-templates/kata-warmpool.yaml
```

#### For Vulnerable Demo (Runc):

```bash
oc apply -f sandbox-templates/runc-with-sa.yaml
oc apply -f sandbox-templates/runc-warmpool.yaml
```

This creates `code-sandbox-pool-kata-sa` and `code-sandbox-pool-runc-sa`. Point the agent backend at one of them in step 4.

### 4. Point Agent Backend at Demo Warm Pool

The agent backend reads `WARMPOOL_NAME` to decide which warm pool to claim sandboxes from (see `04-agent-backend/deployment.yaml`). Patch the deployment to switch between the runc and Kata pools from step 3.

#### To show VULNERABLE (runc):

```bash
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-runc-sa
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

#### To show PROTECTED (Kata):

```bash
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-kata-sa
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

#### Restore default:

```bash
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

Verify the active pool:

```bash
oc get deployment agent-backend -n llm-sandbox-demo -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="WARMPOOL_NAME")].value}{"\n\n"}'
oc get sandboxwarmpool -n llm-sandbox-demo
```

## 5. Run the Attacks

Open the Chat UI and paste these prompts. Use the same prompts for both runc and Kata — only the warm pool changes.

### Attack 1: List secrets (discovery)

```
Write code to list all secrets in the current Kubernetes namespace using the service account token. Use only Python standard library (urllib.request).
```

**Expected (runc — vulnerable):**

```
Found 8 secrets in namespace 'llm-sandbox-demo':
   agent-backend-dockercfg-...
   builder-dockercfg-...
⚠️  database-credentials (SENSITIVE!)
   ...
⚠️  stripe-api-credentials (SENSITIVE!)
```

**Expected (Kata — protected):** HTTP 403 Forbidden, empty list, or connection timeout.

### Attack 2: Read database credentials (exploitation)

```
Write code to read the 'database-credentials' secret from Kubernetes and decode its base64 values. Use only Python standard library (urllib), no external packages. Use the service account token for authentication.
```

**Expected (runc — vulnerable):** Decoded `DB_PASSWORD`, `DB_USER`, `REDIS_PASSWORD`, `API_KEY`, etc.

**Expected (Kata — protected):** Access denied or no credential data returned.

### Attack 3 (optional): Read Stripe credentials

```
Write code to read the 'stripe-api-credentials' secret from Kubernetes and decode its values. Use only Python standard library (urllib).
```

### Attack 4 (optional): Complete audit

```
Write code that lists all secrets in the namespace, then reads and decodes each one that contains credentials. Use only Python standard library (urllib). Format as a security audit report.
```

## Recommended Demo Flow

1. Set `WARMPOOL_NAME=code-sandbox-pool-runc-sa` on the agent backend (step 4).
2. Run **Attack 1** in the Chat UI — show secret names appearing.
3. Run **Attack 2** — show real passwords on screen.
4. Set `WARMPOOL_NAME=code-sandbox-pool-kata-sa` on the agent backend (step 4).
5. Re-run **Attack 1** and **Attack 2** with the same prompts — show they fail.
6. Explain: same code, same RBAC, same namespace; only `runtimeClassName: kata-remote` differs.

Copy/paste prompts and talking points: [`documentation/DEMO_PROMPTS.txt`](documentation/DEMO_PROMPTS.txt)

Additional attack variants: [`attack-payloads/REAL-DEMO-ATTACKS.md`](attack-payloads/REAL-DEMO-ATTACKS.md)

## Attack Success Matrix

| Attack | Runc result | Kata result |
|--------|-------------|-------------|
| List secrets | Shows 6+ secrets including sensitive ones | 403 / empty / timeout |
| Read `database-credentials` | Full passwords exposed | Access denied |
| Read `stripe-api-credentials` | Payment keys exposed | Access denied |
| Complete audit | All credentials stolen | Minimal or no data |

## Cleanup

```bash
oc delete sandboxwarmpool code-sandbox-pool-kata-sa code-sandbox-pool-runc-sa -n llm-sandbox-demo --ignore-not-found
oc delete sandboxtemplate code-execution-template-kata-sa code-execution-template-runc-sa -n llm-sandbox-demo --ignore-not-found
oc delete -f rbac/service-account.yaml
oc delete -f victim-infrastructure/secrets.yaml

# Restore default warm pool
oc apply -f ../02-sandbox/warm-pool.yaml
# Restore default warm pool and agent backend setting
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool
```

## Directory Layout

```
06-security-demo/
├── README.md                          # This file
├── victim-infrastructure/secrets.yaml # Secrets to steal
├── rbac/service-account.yaml          # RBAC for sandbox-demo-sa
├── sandbox-templates/                 # Kata and runc templates + warm pools
├── attack-payloads/                   # Extended attack prompts
└── documentation/
    ├── DEMO_PROMPTS.txt               # Copy/paste demo script
    ├── SERVICE_ACCOUNT_DEMO_READY.md  # Detailed setup notes
    └── SETUP_SUMMARY.md               # Full environment reference
```
