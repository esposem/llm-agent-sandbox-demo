import logging
import os
import re
import sys

import k8s_agent_sandbox.constants as _consts
import k8s_agent_sandbox.k8s_helper as _kh
_consts.CLAIM_API_VERSION = "v1beta1"
_consts.SANDBOX_API_VERSION = "v1beta1"
_kh.CLAIM_API_VERSION = "v1beta1"
_kh.SANDBOX_API_VERSION = "v1beta1"

from k8s_agent_sandbox import SandboxClient
from k8s_agent_sandbox.models import SandboxDirectConnectionConfig

logger = logging.getLogger(__name__)

WARMPOOL_NAME = os.getenv("WARMPOOL_NAME", "code-sandbox-pool")
TEMPLATE_NAME = "code-execution-template"
NAMESPACE = os.getenv("SANDBOX_NAMESPACE", "runc-warmpool")
SANDBOX_TIMEOUT = 120
ROUTER_URL = "http://sandbox-router-svc.agent-sandbox-system.svc.cluster.local:8080"

PREINSTALLED = {
    "os", "sys", "json", "math", "re", "datetime", "time", "collections",
    "itertools", "functools", "pathlib", "io", "csv", "random", "string",
    "hashlib", "base64", "subprocess", "shutil", "glob", "tempfile",
    "typing", "dataclasses", "enum", "copy", "textwrap", "argparse",
    "unittest", "pprint", "logging", "traceback", "contextlib",
    "numpy", "np", "pandas", "pd", "scipy", "sympy", "matplotlib",
    "plt", "requests", "sklearn", "lightgbm",
    "kubernetes", "yaml",
}

IMPORT_RE = re.compile(
    r'^\s*(?:import|from)\s+([\w]+)', re.MULTILINE
)

CMD_RE = re.compile(r'\b([a-z][\w.-]*)\b')

APT_PKG_MAP = {
    "curl": "curl", "wget": "wget", "jq": "jq", "git": "git",
    "vim": "vim", "nano": "nano", "tree": "tree",
    "zip": "zip", "unzip": "unzip", "rsync": "rsync",
    "ssh": "openssh-client", "scp": "openssh-client", "sftp": "openssh-client",
    "dig": "dnsutils", "nslookup": "dnsutils", "host": "dnsutils",
    "ping": "iputils-ping", "traceroute": "traceroute",
    "netstat": "net-tools", "ifconfig": "net-tools",
    "htop": "htop", "bc": "bc", "file": "file",
    "socat": "socat", "nc": "netcat-openbsd", "ncat": "nmap",
    "xmllint": "libxml2-utils", "xsltproc": "xsltproc",
    "convert": "imagemagick", "ffmpeg": "ffmpeg",
    "sqlite3": "sqlite3", "psql": "postgresql-client",
    "mysql": "default-mysql-client",
    "node": "nodejs", "npm": "npm",
}


def _extract_pip_packages(code: str) -> list:
    modules = set(IMPORT_RE.findall(code))
    to_install = modules - PREINSTALLED
    pkg_map = {"sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "Pillow",
               "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv"}
    return [pkg_map.get(m, m) for m in to_install]


def _extract_apt_packages(code: str) -> list:
    words = set(CMD_RE.findall(code))
    pkgs = set()
    for word in words:
        if word in APT_PKG_MAP:
            pkgs.add(APT_PKG_MAP[word])
    return sorted(pkgs)


def verify_warmpool():
    try:
        from kubernetes import client, config
        config.load_incluster_config()
    except Exception:
        logger.warning("Not running in-cluster, skipping warm pool check")
        return

    api = client.CustomObjectsApi()
    try:
        api.get_namespaced_custom_object(
            group="extensions.agents.x-k8s.io",
            version="v1beta1",
            namespace=NAMESPACE,
            plural="sandboxwarmpools",
            name=WARMPOOL_NAME,
        )
        logger.info("Warm pool '%s' found in namespace '%s'", WARMPOOL_NAME, NAMESPACE)
    except client.ApiException as e:
        if e.status == 404:
            logger.warning(
                "Warm pool '%s' not found in namespace '%s'. "
                "Sandbox execution will fail until it is created.",
                WARMPOOL_NAME, NAMESPACE,
            )
        else:
            logger.warning("Cannot verify warm pool (HTTP %s): %s", e.status, e.reason)


def create_client() -> SandboxClient:
    return SandboxClient(
        connection_config=SandboxDirectConnectionConfig(api_url=ROUTER_URL)
    )


def execute_code(client: SandboxClient, language: str, code: str) -> dict:
    sandbox = None
    try:
        sandbox = client.create_sandbox(
            warmpool=WARMPOOL_NAME,
            namespace=NAMESPACE,
            sandbox_ready_timeout=SANDBOX_TIMEOUT,
        )

        if language == "python" or language not in ("bash", "sh", "shell", "javascript"):
            pkgs = _extract_pip_packages(code)
            if pkgs:
                logger.info("Installing pip packages: %s", pkgs)
                pip_result = sandbox.commands.run(f"pip install --quiet {' '.join(pkgs)}")
                if pip_result.exit_code != 0:
                    logger.warning("pip install failed: %s", pip_result.stderr)
                    return {
                        "stdout": "",
                        "stderr": f"Failed to install packages {pkgs}:\n{pip_result.stderr}",
                        "exit_code": pip_result.exit_code,
                        "sandbox_name": sandbox.claim_name,
                    }
            sandbox.files.write("run.py", code)
            result = sandbox.commands.run("timeout 60 python run.py")
        elif language in ("bash", "sh", "shell"):
            apt_pkgs = _extract_apt_packages(code)
            if apt_pkgs:
                logger.info("Installing apt packages: %s", apt_pkgs)
                apt_result = sandbox.commands.run(
                    f"apt-get update -qq && apt-get install -y -qq {' '.join(apt_pkgs)}"
                )
                if apt_result.exit_code != 0:
                    logger.warning("apt install failed: %s", apt_result.stderr)
            sandbox.files.write("run.sh", code)
            result = sandbox.commands.run("timeout 60 bash run.sh")
        elif language == "javascript":
            apt_pkgs = _extract_apt_packages(code)
            if apt_pkgs:
                logger.info("Installing apt packages: %s", apt_pkgs)
                apt_result = sandbox.commands.run(
                    f"apt-get update -qq && apt-get install -y -qq {' '.join(apt_pkgs)}"
                )
                if apt_result.exit_code != 0:
                    logger.warning("apt install failed: %s", apt_result.stderr)
            sandbox.files.write("run.js", code)
            result = sandbox.commands.run("timeout 60 node run.js")

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "sandbox_name": sandbox.claim_name,
        }

    except Exception as e:
        logger.exception("Sandbox execution failed")
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1,
            "sandbox_name": sandbox.claim_name if sandbox else "N/A",
        }
    finally:
        if sandbox:
            try:
                sandbox.terminate()
            except Exception:
                logger.warning("Failed to terminate sandbox", exc_info=True)
