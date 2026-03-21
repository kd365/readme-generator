# src/security_scan/lambda_function.py
"""Lambda that scans a cloned repo for security issues.
Runs as a parallel step in Step Functions alongside analytical agents."""

import json
import os
import re
import subprocess
import shutil

SENSITIVE_PATTERNS = [
    (r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[=:]\s*['\"][^'\"]+['\"]", "Hardcoded secret/API key"),
    (r"(?i)aws_access_key_id\s*=", "AWS access key"),
    (r"(?i)aws_secret_access_key\s*=", "AWS secret key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key ID"),
]

SENSITIVE_FILES = [".env", ".pem", ".key", "credentials", "id_rsa", ".pfx", ".p12"]

SKIP_PATTERNS = [
    r"\.test\.", r"\.spec\.", r"_test\.", r"test[-_]helpers?",
    r"/[Tt]ests?/", r"__tests__/", r"\.e2e\.", r"e2e[-_]",
    r"harness", r"test-.*\.(sh|ts|js|py)$",
    r"\.md$", r"docs/", r"\.example", r"\.sample",
    r"fixtures?/", r"mocks?/", r"\.detect-secrets",
    r"SKILL\.md$", r"README\.md$",
]

MAX_FILE_SIZE = 5000


def should_skip(filepath):
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    return False


def handler(event, context):
    """Clone the repo and run security scan. Returns findings list."""
    repo_url = event.get("repo_url", "")
    print(f"Security scan: {repo_url}")

    if not repo_url:
        return {"findings": [], "error": "Missing repo_url"}

    repo_dir = "/tmp/repo"
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--config", "core.hooksPath=/dev/null",
             repo_url, repo_dir],
            check=True, capture_output=True, text=True, timeout=80
        )
    except Exception as e:
        return {"findings": [], "error": f"Clone failed: {str(e)}"}

    # List files
    file_list = []
    for root, dirs, files in os.walk(repo_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            file_list.append(os.path.relpath(os.path.join(root, name), repo_dir))

    findings = []

    # Check for missing .gitignore
    if ".gitignore" not in file_list:
        findings.append("No `.gitignore` file found — all files may be tracked including sensitive ones.")

    # Check for missing LICENSE
    has_license = any(f.lower() in ("license", "license.md", "license.txt") for f in file_list)
    if not has_license:
        findings.append("No `LICENSE` file found — usage terms are undefined.")

    # Check for sensitive files
    for f in file_list:
        basename = os.path.basename(f).lower()
        if basename in (".env.example", ".env.sample"):
            continue
        for pattern in SENSITIVE_FILES:
            if basename == pattern or basename.endswith(pattern):
                findings.append(f"Potentially sensitive file committed: `{f}`")

    # Scan source files for hardcoded secrets
    secret_count = 0
    for f in file_list:
        if should_skip(f):
            continue
        full_path = os.path.join(repo_dir, f)
        try:
            if os.path.getsize(full_path) > MAX_FILE_SIZE:
                continue
            with open(full_path, "r", errors="replace") as fh:
                content = fh.read()
                for pattern, desc in SENSITIVE_PATTERNS:
                    if re.search(pattern, content):
                        if secret_count < 10:
                            findings.append(f"{desc} found in `{f}`")
                        secret_count += 1
                        break
        except Exception:
            pass

    if secret_count > 10:
        findings.append(f"... and {secret_count - 10} more file(s) with potential secrets.")

    # Clean up
    shutil.rmtree(repo_dir, ignore_errors=True)

    print(f"Security scan complete: {len(findings)} finding(s)")
    return {"findings": findings}
