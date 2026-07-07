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

## Quick Start

### 1. Deploy the victim pod

A simulated payment service with credentials in its environment variables:

```bash
oc apply -f victim-pod/deployment.yaml
oc rollout status deployment/payment-service -n llm-sandbox-demo
```

Verify it's running:

```bash
oc get pods -n llm-sandbox-demo -l app=payment-service
```

### 2. Deploy RBAC (SCC + ServiceAccount)

Create a custom SecurityContextConstraints that allows `hostPID` and a service account bound to it:

```bash
oc apply -f rbac/scc-and-sa.yaml
```

This creates:
- **ServiceAccount**: `hostpid-demo-sa`
- **SCC**: `sandbox-hostpid-demo` (allows `hostPID` and `privileged`)

The SCC grants privileged access so the container can read `/proc/<pid>/environ` across container boundaries (SELinux otherwise blocks this). This simulates a misconfigured namespace where a debugging or monitoring tool has been granted elevated privileges.

### 3. Deploy sandbox templates and warm pools

```bash
oc apply -f sandbox-templates/
```

This creates:
- `code-execution-template-runc-hostpid` — runc with `hostPID: true` (VULNERABLE)
- `code-execution-template-kata-hostpid` — kata with `hostPID: true` (PROTECTED)
- `code-sandbox-pool-runc-hostpid` — warm pool for runc
- `code-sandbox-pool-kata-hostpid` — warm pool for kata

Wait for warm pools to be ready:

```bash
oc get sandboxwarmpool -n llm-sandbox-demo
```

### 4. Point agent backend at the runc pool (vulnerable)

```bash
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-runc-hostpid
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

### 5. Run the attack

Open the Chat UI and paste the credential theft code from [`attack-payloads/ATTACKS.md`](attack-payloads/ATTACKS.md), then click **Run**.

With runc, you'll see credentials stolen from the payment-service pod:

```
PID 4521: python3 -c import time; print('Payment service running...')
  STOLEN  DB_PASSWORD = xK9#mP2$vL5nQ8wR!jF3
  STOLEN  STRIPE_SECRET_KEY = sk_live_51Hb3gK2eZvKYlo2C0EXAMPLE
  STOLEN  AWS_SECRET_ACCESS_KEY = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

### 6. Switch to kata (protected)

```bash
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool-kata-hostpid
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

### 7. Re-run the same attack

Same code, same pod spec, same `hostPID: true`. Output:

```
RESULT: No other pods' processes visible.
VM isolation is working - /proc only shows this VM's processes.
```

## Why this works

**With runc**: The container shares the node's kernel. `hostPID: true` exposes every process on the node in `/proc`, and `privileged: true` grants the SELinux context (`spc_t`) needed to read `/proc/<pid>/environ` across container boundaries. Together, this lets the attack code read environment variables — passwords, API keys, tokens — from any pod on the same node.

**With kata**: The pod runs inside a lightweight VM with its own kernel. Even though the pod spec has `hostPID: true` and `privileged: true`, the VM's `/proc` only contains processes inside the VM itself. There are no other pods' processes to read. Kata neutralizes the dangerous pod spec at the hardware level — no configuration change, no RBAC tuning needed.

## Demo Flow (recommended)

1. Show the victim pod running: `oc get pods -l app=payment-service`
2. Point out its env vars contain secrets: `oc set env deployment/payment-service --list`
3. Set `WARMPOOL_NAME=code-sandbox-pool-runc-hostpid` (runc)
4. Run **Attack 1** (credential theft) — show passwords on screen
5. Run **Attack 2** (host recon) — show real node kernel, 300+ processes, 32GB RAM
6. Switch to `WARMPOOL_NAME=code-sandbox-pool-kata-hostpid` (kata)
7. Re-run the same attacks — show they return nothing
8. Key message: **same pod spec, same RBAC — only the runtime changed**

## Attack Success Matrix

| Attack | runc | kata |
|--------|------|------|
| Steal env vars from other pods | Passwords, API keys visible | No processes visible |
| Host kernel version | Real node kernel (CVE target) | VM guest kernel only |
| Process count | 300+ (all node processes) | < 10 (VM only) |
| Read other pods' files via /proc | Service account tokens, configs | No access |

## Note on node co-location

The victim pod and sandbox pod must run on the **same node** for the hostPID attack to work (hostPID exposes processes on the local node only). In a small demo cluster this happens naturally. If needed, use node selectors or affinity rules to force co-location.

## Cleanup

```bash
# Remove demo resources
oc delete -f sandbox-templates/
oc delete -f rbac/scc-and-sa.yaml
oc delete -f victim-pod/deployment.yaml

# Restore default warm pool
oc set env deployment/agent-backend -n llm-sandbox-demo WARMPOOL_NAME=code-sandbox-pool
oc rollout status deployment/agent-backend -n llm-sandbox-demo
```

## Directory Layout

```
07-new-demo/
├── README.md                        # This file
├── victim-pod/
│   └── deployment.yaml              # Payment service with secrets in env vars
├── rbac/
│   └── scc-and-sa.yaml             # Custom SCC (hostPID) + ServiceAccount
├── sandbox-templates/
│   ├── runc-hostpid.yaml           # Runc template with hostPID (vulnerable)
│   ├── runc-warmpool.yaml          # Warm pool for runc
│   ├── kata-hostpid.yaml           # Kata template with hostPID (protected)
│   └── kata-warmpool.yaml          # Warm pool for kata
└── attack-payloads/
    └── ATTACKS.md                   # Attack code + expected output
```
