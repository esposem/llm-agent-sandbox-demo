# Security Demo: Kernel-Level Isolation (Kata vs Runc)

This demo shows that Kata Containers provide **real VM-level isolation** that runc cannot, even with identical pod specs and RBAC.

The sandbox pod runs with `hostPID: true` and `privileged: true`. In runc, this gives full access to every process on the node — the attack code reads `/proc/<pid>/environ` to steal environment variables (passwords, API keys) from a victim pod. In kata, the pod runs inside a VM with its own kernel, so `/proc` only shows VM-internal processes and the attack finds nothing.

| | runc | kata |
|---|---|---|
| **Kernel** | Shared with the node | Isolated guest VM kernel |
| **`/proc`** | All node processes visible | Only VM processes visible |
| **Env var theft** | Read other pods' env vars via `/proc/<pid>/environ` | No other-pod processes exist in the VM |
| **Host fingerprinting** | Real node kernel, CPU, memory | VM kernel, vCPU, allocated RAM |

The pod spec is **identical** for both runtimes — same `hostPID`, same `privileged`, same service account. The only difference is `runtimeClassName: kata-remote`. Kata neutralizes the dangerous pod spec at the hardware level.

## How It Works

In the workshop deployment (Helm chart via ArgoCD), the security demo is **pre-deployed** as part of the chart — no manual setup needed. The Helm chart creates:

- **Two warm pool namespaces**: `runc-warmpool` (runc runtime) and `kata-warmpool` (kata-remote runtime)
- **Victim pod**: `payment-service` in the `victim` namespace with fake credentials in env vars
- **SCC**: `sandbox-hostpid-demo` granting `hostPID` + `privileged` to sandbox SAs in both namespaces
- **SandboxTemplates**: `code-execution-template` in each namespace (runc vs kata-remote), with pod affinity to the victim node
- **WarmPools**: `code-sandbox-pool` in each namespace (2 replicas each)
- **RBAC**: `agent-backend` SA in `llm-sandbox-demo` has `sandbox-manager` Role in both namespaces

The agent-backend reads `SANDBOX_NAMESPACE` to decide which warm pool to claim pods from. Switching runtimes is a single env var change — no resources are created or deleted.

## Quick Start (Workshop Mode)

Everything is already deployed. Just run the switch scripts:

### 1. Verify the victim pod is running

```bash
oc get pods -n victim -l app=payment-service
oc set env deployment/payment-service -n victim --list
```

### 2. Verify warm pools are ready

```bash
oc get sandboxwarmpools -n runc-warmpool
oc get sandboxwarmpools -n kata-warmpool
```

### 3. Run the attack (runc — vulnerable)

The agent-backend starts pointing at `runc-warmpool`. Open the Chat UI and paste the credential theft code from [`attack-payloads/ATTACKS.md`](attack-payloads/ATTACKS.md), then click **Run**.

You'll see credentials stolen from the payment-service pod:

```
PID 4521: python3 -c import time; print('Payment service running...')
  STOLEN  DB_PASSWORD = xK9#mP2$vL5nQ8wR!jF3
  STOLEN  STRIPE_SECRET_KEY = sk_live_51Hb3gK2eZvKYlo2C0EXAMPLE
  STOLEN  AWS_SECRET_ACCESS_KEY = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

### 4. Switch to kata (protected)

```bash
./switch-to-kata.sh
```

This patches `SANDBOX_NAMESPACE=kata-warmpool` on the agent-backend and waits for the rollout.

### 5. Re-run the same attack

Same code, same pod spec, same `hostPID: true`. Output:

```
RESULT: No other pods' processes visible.
VM isolation is working - /proc only shows this VM's processes.
```

### 6. Switch back to runc

```bash
./switch-to-runc.sh
```

## Quick Start (Manual Deployment)

If deploying manually (not via the Helm chart), deploy the standalone resources:

```bash
# Deploy victim pod
oc create namespace victim --dry-run=client -o yaml | oc apply -f -
oc apply -f victim-pod/deployment.yaml
oc rollout status deployment/payment-service -n victim

# Deploy RBAC (SCC + ServiceAccount)
oc apply -f rbac/scc-and-sa.yaml

# Deploy sandbox templates and warm pools
oc apply -f sandbox-templates/
```

Then use `oc set env` to switch the agent-backend between warm pool names:

```bash
# Runc (vulnerable)
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-runc-hostpid

# Kata (protected)
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-kata-hostpid
```

## Why This Works

**With runc**: The container shares the node's kernel. `hostPID: true` exposes every process on the node in `/proc`, and `privileged: true` grants the SELinux context (`spc_t`) needed to read `/proc/<pid>/environ` across container boundaries. Together, this lets the attack code read environment variables — passwords, API keys, tokens — from any pod on the same node.

**With kata**: The pod runs inside a lightweight VM with its own kernel. Even though the pod spec has `hostPID: true` and `privileged: true`, the VM's `/proc` only contains processes inside the VM itself. There are no other pods' processes to read. Kata neutralizes the dangerous pod spec at the hardware level — no configuration change, no RBAC tuning needed.

## Demo Flow (recommended)

1. Show the victim pod running: `oc get pods -n victim -l app=payment-service`
2. Point out its env vars contain secrets: `oc set env deployment/payment-service -n victim --list`
3. Confirm agent-backend targets runc: `oc get deployment agent-backend -n llm-sandbox-demo -o jsonpath='{.spec.template.spec.containers[0].env}'`
4. Run **Attack 1** (credential theft) — show passwords on screen
5. Run **Attack 2** (host recon) — show real node kernel, 300+ processes, 32GB RAM
6. Switch to kata: `./switch-to-kata.sh`
7. Re-run the same attacks — show they return nothing
8. Key message: **same pod spec, same RBAC — only the runtime changed**

## Attack Success Matrix

| Attack | runc | kata |
|--------|------|------|
| Steal env vars from other pods | Passwords, API keys visible | No processes visible |
| Host kernel version | Real node kernel (CVE target) | VM guest kernel only |
| Process count | 300+ (all node processes) | < 10 (VM only) |
| Read other pods' files via /proc | Service account tokens, configs | No access |

## Directory Layout

```
06-security-demo/
├── README.md                        # This file
├── switch-to-kata.sh                # Switch agent-backend to kata-warmpool namespace
├── switch-to-runc.sh                # Switch agent-backend back to runc-warmpool namespace
├── victim-pod/
│   └── deployment.yaml              # Payment service with secrets in env vars
├── rbac/
│   └── scc-and-sa.yaml             # Custom SCC (hostPID + privileged) + ServiceAccount
├── sandbox-templates/               # Standalone templates (for manual deployment only)
│   ├── runc-hostpid.yaml           # Runc template with hostPID (vulnerable)
│   ├── runc-warmpool.yaml          # Warm pool for runc
│   ├── kata-hostpid.yaml           # Kata template with hostPID (protected)
│   └── kata-warmpool.yaml          # Warm pool for kata
└── attack-payloads/
    └── ATTACKS.md                   # Attack code + expected output
```

In workshop mode (Helm chart), the resources in `victim-pod/`, `rbac/`, and `sandbox-templates/` are deployed by `bootstrap/templates/sandbox.yaml` and do not need to be applied manually.
