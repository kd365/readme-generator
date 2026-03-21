# src/scanner_direct/lambda_function.py
"""Direct scanner Lambda for Step Functions — bypasses the Bedrock Agent
to return structured JSON instead of a narrative summary.
Reuses the repo_scanner logic but with a simple event format."""

import json
import os
import subprocess
import shutil

KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
    "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "LICENSE", "CONTRIBUTING.md",
]

MAX_FILE_SIZE = 5000
MAX_RESPONSE_SIZE = 20000

PRIORITY_FILES = [
    "package.json", "requirements.txt", "setup.py", "pyproject.toml",
    "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "Dockerfile", "docker-compose.yml", ".env.example", "Makefile",
]


def handler(event, context):
    """Clone a repo and return structured JSON with file list + key contents."""
    repo_url = event.get("repo_url", "")
    print(f"Direct scanner: cloning {repo_url}")

    if not repo_url:
        return {"error": "Missing repo_url", "files": [], "key_file_contents": {}}

    repo_dir = "/tmp/repo"
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, repo_dir],
            check=True, capture_output=True, text=True, timeout=80
        )
    except subprocess.TimeoutExpired:
        return {"error": "Clone timed out", "files": [], "key_file_contents": {}}
    except subprocess.CalledProcessError as e:
        return {"error": f"Clone failed: {e.stderr.strip()}", "files": [], "key_file_contents": {}}

    # List files
    file_list = []
    for root, dirs, files in os.walk(repo_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            file_list.append(os.path.relpath(os.path.join(root, name), repo_dir))

    # Read key files
    key_contents = {}
    for f in file_list:
        if os.path.basename(f) in KEY_FILES or f in KEY_FILES:
            full_path = os.path.join(repo_dir, f)
            try:
                size = os.path.getsize(full_path)
                with open(full_path, "r", errors="replace") as fh:
                    if size <= MAX_FILE_SIZE:
                        key_contents[f] = fh.read()
                    else:
                        key_contents[f] = fh.read(MAX_FILE_SIZE) + "\n... [truncated]"
            except Exception:
                pass

    result = {"files": file_list, "key_file_contents": key_contents}

    # Truncate if too large
    response = json.dumps(result)
    if len(response) > MAX_RESPONSE_SIZE:
        print(f"Response too large ({len(response)} chars), truncating...")
        trimmed = {"files": file_list, "key_file_contents": {}}
        for k, v in key_contents.items():
            if os.path.basename(k) in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:3000] if len(v) > 3000 else v
        for k, v in key_contents.items():
            if os.path.basename(k) not in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:2000] if len(v) > 2000 else v
                if len(json.dumps(trimmed)) > MAX_RESPONSE_SIZE:
                    break
        if len(json.dumps(trimmed)) > MAX_RESPONSE_SIZE:
            trimmed["files"] = trimmed["files"][:500]
        result = trimmed

    print(f"Returning {len(result['files'])} files, {len(result.get('key_file_contents', {}))} key files")
    return result
