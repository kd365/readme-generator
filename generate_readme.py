#!/usr/bin/env python3
"""Interactive CLI for generating README files from GitHub repositories."""

import boto3
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.live import Live
    from rich.table import Table
except ImportError:
    print("Missing 'rich' library. Install with: pip install rich")
    sys.exit(1)

console = Console()

# Agent IDs — match Terraform-deployed agents with -KH suffix
AGENTS = {
    "scanner":    {"id": "HEWWJ08EGK", "name": "Repo Scanner"},
    "summarizer": {"id": "6AGKFKSMNY", "name": "Project Summarizer"},
    "install":    {"id": "7RE7ZRGOW2", "name": "Installation Guide"},
    "usage":      {"id": "XF6FJ6D4PL", "name": "Usage Examples"},
    "compiler":   {"id": "TOP3NIWUQG", "name": "Final Compiler"},
}

ALIAS_ID = "TSTALIASID"

KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "Pipfile",
    "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle", "environment.yml",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "LICENSE", "CONTRIBUTING.md",
]

MAX_FILE_SIZE = 5000

client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

# Session state
session = {
    "repo_url": None,
    "repo_name": None,
    "clone_dir": None,
    "scan_data": None,
    "history": [],
}


# --- Startup Validation ---

def check_prerequisites():
    """Verify AWS credentials and gh CLI are available."""
    errors = []

    # Check AWS credentials
    try:
        sts = boto3.client("sts")
        sts.get_caller_identity()
    except Exception:
        errors.append("AWS credentials not configured. Run 'aws configure' or set AWS_PROFILE.")

    # Check gh CLI
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        errors.append("GitHub CLI not authenticated. Run 'gh auth login'.")

    if errors:
        for e in errors:
            console.print(f"  [red]{e}[/red]")
        console.print()
        console.print("  [yellow]Some features may not work. Continue anyway? (y/n)[/yellow]")
        if input("  > ").strip().lower() != "y":
            sys.exit(1)


def validate_github_url(url):
    """Check if the URL looks like a valid GitHub repo."""
    pattern = r"^https?://github\.com/[\w.-]+/[\w.-]+/?$"
    return bool(re.match(pattern, url))


# --- Local Repo Scanning ---

def list_existing_projects(output_dir="./projects"):
    """List repos already cloned in the projects directory."""
    if not os.path.exists(output_dir):
        return []
    return [d for d in sorted(os.listdir(output_dir))
            if os.path.isdir(os.path.join(output_dir, d)) and not d.startswith(".")]


def load_existing_project(output_dir="./projects"):
    """Load scan data from an already-cloned project."""
    projects = list_existing_projects(output_dir)
    if not projects:
        console.print("  [dim]No projects found in ./projects/[/dim]")
        return None

    console.print("\n  [bold]Existing projects:[/bold]")
    for i, name in enumerate(projects, 1):
        clone_path = os.path.join(output_dir, name)
        file_count = sum(1 for _, _, files in os.walk(clone_path) for _ in files)
        console.print(f"    {i}) {name} ({file_count} files)")

    choice = console.input("\n  Select project number (or 'c' to cancel)> ").strip()
    if choice.lower() == "c":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(projects):
            return scan_existing_project(projects[idx], output_dir)
        else:
            console.print("  [red]Invalid selection.[/red]")
            return None
    except ValueError:
        console.print("  [red]Invalid input.[/red]")
        return None


def scan_existing_project(repo_name, output_dir="./projects"):
    """Scan an already-cloned repo directory."""
    clone_path = os.path.join(output_dir, repo_name)
    if not os.path.exists(clone_path):
        console.print(f"  [red]Directory {clone_path} not found.[/red]")
        return None

    clone_real = os.path.realpath(clone_path)
    file_list = []
    for root, dirs, files in os.walk(clone_path):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            rel_path = os.path.relpath(os.path.join(root, name), clone_path)
            if ".." in rel_path:
                continue
            full_path = os.path.join(clone_path, rel_path)
            if os.path.islink(full_path) and not os.path.realpath(full_path).startswith(clone_real):
                continue
            file_list.append(rel_path)

    key_contents = {}
    for f in file_list:
        if os.path.basename(f) in KEY_FILES or f in KEY_FILES:
            full_path = os.path.join(clone_path, f)
            try:
                if os.path.islink(full_path):
                    continue
                size = os.path.getsize(full_path)
                with open(full_path, "r", errors="replace") as fh:
                    if size <= MAX_FILE_SIZE:
                        key_contents[f] = fh.read()
                    else:
                        key_contents[f] = fh.read(MAX_FILE_SIZE) + "\n... [truncated]"
            except Exception:
                pass

    scan_data = {"files": file_list, "key_file_contents": key_contents}

    session["repo_url"] = None  # No URL for existing projects
    session["repo_name"] = repo_name
    session["clone_dir"] = clone_path
    session["scan_data"] = scan_data

    console.print(f"  [green]Loaded {repo_name}: {len(file_list)} file(s), {len(key_contents)} key file(s)[/green]")
    return scan_data


def clone_and_scan(repo_url, output_dir="./projects"):
    """Clone a repo locally and scan its files + key contents."""
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    clone_path = os.path.join(output_dir, repo_name)

    if os.path.exists(clone_path):
        console.print(f"  [yellow]Directory {clone_path} already exists.[/yellow]")
        choice = console.input("  Reuse existing clone? (y/n)> ").strip().lower()
        if choice == "y":
            return scan_existing_project(repo_name, output_dir)
        shutil.rmtree(clone_path)

    os.makedirs(output_dir, exist_ok=True)

    with Live(Spinner("dots", text=f"  Cloning {repo_url}..."), console=console, refresh_per_second=10):
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--config", "core.hooksPath=/dev/null", repo_url, clone_path],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            console.print("  [red]Clone timed out after 120s. Try a smaller repo.[/red]")
            return None
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip()
            if "not found" in stderr.lower() or "repository" in stderr.lower():
                console.print("  [red]Repository not found. Check the URL and ensure it's public.[/red]")
            elif "authentication" in stderr.lower():
                console.print("  [red]Repository requires authentication. Only public repos are supported.[/red]")
            else:
                console.print(f"  [red]Clone failed: {stderr}[/red]")
            return None

    # Walk and list files — with security checks
    file_list = []
    warnings = []
    checks_passed = []
    clone_real = os.path.realpath(clone_path)
    symlink_count = 0
    traversal_count = 0

    for root, dirs, files in os.walk(clone_path):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            rel_path = os.path.relpath(os.path.join(root, name), clone_path)

            # Path traversal check — skip files that escape the clone directory
            if ".." in rel_path:
                warnings.append(f"Path traversal detected, skipping: {rel_path}")
                traversal_count += 1
                continue

            full_path = os.path.join(clone_path, rel_path)

            # Symlink check — skip symlinks that point outside the repo
            if os.path.islink(full_path):
                target = os.path.realpath(full_path)
                if not target.startswith(clone_real):
                    warnings.append(f"Symlink escapes repo, skipping: {rel_path} → {target}")
                    symlink_count += 1
                    continue

            file_list.append(rel_path)

    # Warn about dangerous IDE/devcontainer files
    dangerous_files = [f for f in file_list if f in (
        ".vscode/tasks.json", ".vscode/launch.json",
        ".devcontainer/devcontainer.json", ".devcontainer/Dockerfile",
    ) or f.startswith(".devcontainer/")]

    # Check total clone size
    total_size = sum(
        os.path.getsize(os.path.join(clone_path, f))
        for f in file_list
        if os.path.exists(os.path.join(clone_path, f))
    )
    size_mb = total_size / (1024 * 1024)

    # Build security report
    console.print(f"\n  [bold cyan]Clone Security Report:[/bold cyan]")
    checks_passed.append("Git hooks disabled (core.hooksPath=/dev/null)")
    if traversal_count == 0:
        checks_passed.append("No path traversal attempts detected")
    if symlink_count == 0:
        checks_passed.append("No malicious symlinks detected")
    else:
        warnings.append(f"{symlink_count} symlink(s) pointing outside repo were blocked")
    if not dangerous_files:
        checks_passed.append("No dangerous IDE auto-run configs found (.vscode/tasks.json, .devcontainer/)")
    else:
        warnings.append(f"Auto-run IDE configs found: {', '.join(dangerous_files[:3])}")
        warnings.append("Do NOT open this project in VS Code without reviewing these files first.")
    checks_passed.append(f"Clone size: {size_mb:.1f}MB")

    for c in checks_passed:
        console.print(f"    [green]PASS[/green] {c}")
    for w in warnings:
        console.print(f"    [red]WARN[/red] {w}")

    if warnings:
        proceed = console.input("\n  Security warnings found. Continue? (y/n)> ").strip().lower()
        if proceed != "y":
            shutil.rmtree(clone_path)
            console.print("  [dim]Clone deleted.[/dim]")
            return None
    else:
        console.print(f"    [bold green]All checks passed.[/bold green]")

    if size_mb > 500:
        console.print(f"  [red]Clone is very large ({size_mb:.0f}MB). Proceed? (y/n)[/red]")
        if console.input("  > ").strip().lower() != "y":
            shutil.rmtree(clone_path)
            return None

    # Read key files — only real files, never symlinks pointing outside
    key_contents = {}
    for f in file_list:
        if os.path.basename(f) in KEY_FILES or f in KEY_FILES:
            full_path = os.path.join(clone_path, f)
            try:
                # Double-check symlink safety before reading
                if os.path.islink(full_path):
                    continue
                size = os.path.getsize(full_path)
                with open(full_path, "r", errors="replace") as fh:
                    if size <= MAX_FILE_SIZE:
                        key_contents[f] = fh.read()
                    else:
                        key_contents[f] = fh.read(MAX_FILE_SIZE) + "\n... [truncated]"
            except Exception:
                pass

    scan_data = {"files": file_list, "key_file_contents": key_contents}

    console.print(f"  [green]Cloned to {clone_path}[/green]")
    console.print(f"  [green]Found {len(file_list)} file(s), read {len(key_contents)} key file(s)[/green]")

    session["repo_url"] = repo_url
    session["repo_name"] = repo_name
    session["clone_dir"] = clone_path
    session["scan_data"] = scan_data

    return scan_data


# --- Agent Invocation ---

def invoke_agent(agent_key, input_text, show_spinner=True):
    """Invoke a Bedrock agent and return the response."""
    agent = AGENTS[agent_key]
    result = ""

    def _call():
        nonlocal result
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = ""
                response = client.invoke_agent(
                    agentId=agent["id"],
                    agentAliasId=ALIAS_ID,
                    sessionId=str(uuid.uuid4()),
                    inputText=input_text,
                )
                for event in response["completion"]:
                    if "chunk" in event:
                        result += event["chunk"]["bytes"].decode("utf-8")
                return  # Success
            except Exception as e:
                if "throttling" in str(e).lower() and attempt < max_retries - 1:
                    wait = (attempt + 1) * 10  # 10s, 20s backoff
                    time.sleep(wait)
                else:
                    raise

    try:
        if show_spinner:
            with Live(Spinner("dots", text=f"  {agent['name']}..."), console=console, refresh_per_second=10):
                _call()
        else:
            _call()
    except Exception as e:
        result = f"Error: {e}"

    # Log to history
    session["history"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "agent": agent["name"],
        "output_preview": result[:120] + "..." if len(result) > 120 else result,
    })

    return result


def check_agent_result(result):
    """Check if an agent result is an error and return a placeholder if so."""
    if result.startswith("Error") or "could not" in result.lower():
        return "This section could not be generated."
    return result


def strip_section_header(text, header):
    """Remove a duplicate section header from agent output (e.g., ## Usage)."""
    lines = text.strip().split("\n")
    if lines and lines[0].strip().lower().startswith(header.lower()):
        return "\n".join(lines[1:]).strip()
    return text.strip()


# --- Post-Processing Validation ---

# Maps install commands to the dependency files that must exist to justify them
COMMAND_FILE_REQUIREMENTS = {
    "pip install": ["requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", "Pipfile"],
    "pip3 install": ["requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", "Pipfile"],
    "pip install -e": ["setup.py", "setup.cfg", "pyproject.toml"],
    "poetry install": ["pyproject.toml"],
    "pipenv install": ["Pipfile"],
    "npm install": ["package.json"],
    "pnpm install": ["package.json"],
    "yarn install": ["package.json"],
    "cargo build": ["Cargo.toml"],
    "go mod": ["go.mod"],
    "bundle install": ["Gemfile"],
    "mvn ": ["pom.xml"],
    "gradle ": ["build.gradle", "build.gradle.kts"],
}


def validate_referenced_commands(readme_content, file_list):
    """Remove code blocks containing install commands for ecosystems not present in the repo."""
    filenames = {os.path.basename(f) for f in file_list}
    lines = readme_content.split("\n")
    cleaned = []
    skip_block = False
    in_code_block = False
    block_buffer = []
    removed_sections = []

    for line in lines:
        if line.strip().startswith("```") and not in_code_block:
            in_code_block = True
            block_buffer = [line]
            skip_block = False
            continue
        elif line.strip().startswith("```") and in_code_block:
            in_code_block = False
            block_buffer.append(line)
            if not skip_block:
                cleaned.extend(block_buffer)
            else:
                removed_sections.append(block_buffer)
            block_buffer = []
            continue

        if in_code_block:
            block_buffer.append(line)
            # Check if this line has a command that requires a file we don't have
            for cmd, required_files in COMMAND_FILE_REQUIREMENTS.items():
                if cmd in line.lower():
                    if not any(rf in filenames for rf in required_files):
                        skip_block = True
                        break
        else:
            # Remove paragraph text that introduces a skipped block
            # (e.g., "### Python Components" followed by a removed pip block)
            cleaned.append(line)

    # Remove orphaned headers — headers followed by nothing until the next header
    final = []
    for i, line in enumerate(cleaned):
        if line.startswith("### "):
            # Look ahead: if next non-empty line is another header or end, skip this header
            remaining = [l for l in cleaned[i+1:] if l.strip()]
            if not remaining or remaining[0].startswith("#"):
                continue
        final.append(line)

    if removed_sections:
        console.print(f"  [yellow]Post-processing: removed {len(removed_sections)} code block(s) with unverified install commands[/yellow]")

    return "\n".join(final)


# --- Security Scan ---

SENSITIVE_PATTERNS = [
    (r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[=:]\s*['\"][^'\"]+['\"]", "Hardcoded secret/API key"),
    (r"(?i)aws_access_key_id\s*=", "AWS access key"),
    (r"(?i)aws_secret_access_key\s*=", "AWS secret key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key ID"),
]

SENSITIVE_FILES = [".env", ".pem", ".key", "credentials", "id_rsa", ".pfx", ".p12"]

# Files to skip during secret scanning (tests, docs, examples)
SKIP_PATTERNS = [
    r"\.test\.", r"\.spec\.", r"_test\.", r"test[-_]helpers?",
    r"/[Tt]ests?/", r"__tests__/", r"\.e2e\.", r"e2e[-_]",
    r"harness", r"test-.*\.(sh|ts|js|py)$",
    r"\.md$", r"docs/", r"\.example", r"\.sample",
    r"fixtures?/", r"mocks?/", r"\.detect-secrets",
    r"SKILL\.md$", r"README\.md$",
]


def should_skip_secret_scan(filepath):
    """Check if a file should be skipped for secret scanning (tests, docs, etc.)."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    return False


def run_security_scan(clone_dir, file_list):
    """Scan the repo for common security issues. Returns a list of findings."""
    findings = []

    # Check for missing .gitignore
    if ".gitignore" not in file_list:
        findings.append("No `.gitignore` file found — all files may be tracked including sensitive ones.")

    # Check for missing LICENSE
    has_license = any(f.lower() in ("license", "license.md", "license.txt") for f in file_list)
    if not has_license:
        findings.append("No `LICENSE` file found — usage terms are undefined.")

    # Check for sensitive files committed (only non-example files)
    for f in file_list:
        basename = os.path.basename(f).lower()
        if basename == ".env.example" or basename == ".env.sample":
            continue
        for pattern in SENSITIVE_FILES:
            if basename == pattern or basename.endswith(pattern):
                findings.append(f"Potentially sensitive file committed: `{f}`")

    # Scan source files (not tests/docs) for hardcoded secrets
    secret_count = 0
    for f in file_list:
        if should_skip_secret_scan(f):
            continue
        full_path = os.path.join(clone_dir, f)
        try:
            if os.path.getsize(full_path) > MAX_FILE_SIZE:
                continue
            with open(full_path, "r", errors="replace") as fh:
                content = fh.read()
                for pattern, desc in SENSITIVE_PATTERNS:
                    if re.search(pattern, content):
                        if secret_count < 10:  # Cap at 10 findings
                            findings.append(f"{desc} found in `{f}`")
                        secret_count += 1
                        break
        except Exception:
            pass

    if secret_count > 10:
        findings.append(f"... and {secret_count - 10} more file(s) with potential secrets.")

    return findings


# --- README Generation Pipeline ---

def generate_readme():
    """Full pipeline: scan → parallel agents → compile → write → optionally push."""
    # Get URL if no scan cached
    if not session["scan_data"]:
        url = console.input("  GitHub URL> ").strip()
        if not url:
            return
        if not validate_github_url(url):
            console.print("  [red]Invalid GitHub URL. Expected: https://github.com/owner/repo[/red]")
            return
        if not clone_and_scan(url):
            return

    scan_data = session["scan_data"]

    # Truncate for large repos — agents have input limits
    MAX_INPUT_SIZE = 25000
    scan_json = json.dumps(scan_data)
    if len(scan_json) > MAX_INPUT_SIZE:
        console.print(f"  [yellow]Large repo ({len(scan_data['files'])} files). Trimming scan data for agents...[/yellow]")

        # Priority files — always include these first (dependency/config files)
        PRIORITY_FILES = [
            "package.json", "requirements.txt", "setup.py", "pyproject.toml",
            "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
            "Dockerfile", "docker-compose.yml", ".env.example", "Makefile",
        ]

        trimmed = {"files": scan_data["files"], "key_file_contents": {}}

        # Add priority files first
        for k, v in scan_data["key_file_contents"].items():
            if os.path.basename(k) in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:3000] if len(v) > 3000 else v

        # Then add remaining files if space allows
        for k, v in scan_data["key_file_contents"].items():
            if os.path.basename(k) not in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:2000] if len(v) > 2000 else v
                if len(json.dumps(trimmed)) > MAX_INPUT_SIZE:
                    break

        scan_json = json.dumps(trimmed)
        console.print(f"  [dim]Kept {len(trimmed['key_file_contents'])} key files in agent input[/dim]")

    # Run 3 analytical agents sequentially (avoids Bedrock throttling)
    console.print("\n  [bold cyan]Running analytical agents...[/bold cyan]\n")
    results = {}

    agent_keys = ["summarizer", "install", "usage"]
    for i, agent_key in enumerate(agent_keys):
        results[agent_key] = invoke_agent(agent_key, scan_json, show_spinner=True)
        if i < len(agent_keys) - 1:
            time.sleep(8)  # Avoid Bedrock throttling between calls

    # Display results and strip duplicate headers
    labels = {"summarizer": "Project Summary", "install": "Getting Started", "usage": "Usage"}
    headers_to_strip = {"summarizer": "## project summary", "install": "## getting started", "usage": "## usage"}
    for key in ["summarizer", "install", "usage"]:
        label = labels[key]
        result = check_agent_result(results[key])
        result = strip_section_header(result, headers_to_strip[key])
        results[key] = result
        console.print(f"  [bold green]{label}:[/bold green]")
        console.print(f"  {result[:200]}{'...' if len(result) > 200 else ''}\n")

    # Run security scan
    console.print("  [bold cyan]Running security scan...[/bold cyan]")
    findings = run_security_scan(session["clone_dir"], session["scan_data"]["files"])
    if findings:
        console.print(f"  [yellow]Found {len(findings)} security note(s)[/yellow]")
        for f in findings:
            console.print(f"    [yellow]- {f}[/yellow]")
    else:
        console.print("  [green]No security issues found[/green]")

    # Build security section
    security_section = ""
    if findings:
        security_section = "\n".join(f"- {f}" for f in findings)

    # Compile
    compiler_input = json.dumps({
        "repository_name": session["repo_name"],
        "project_summary": results["summarizer"],
        "installation_guide": results["install"],
        "usage_examples": results["usage"],
    })

    console.print("\n  [bold cyan]Compiling README...[/bold cyan]")
    time.sleep(8)  # Avoid Bedrock throttling before compiler call
    readme_content = invoke_agent("compiler", compiler_input)

    # Post-processing: strip preamble before first # header
    lines = readme_content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            readme_content = "\n".join(lines[i:])
            break

    # Post-processing: validate referenced files actually exist
    readme_content = validate_referenced_commands(readme_content, session["scan_data"]["files"])

    # Append security section if findings exist
    if security_section:
        readme_content += f"\n\n## Security Notes\n\n{security_section}\n"

    # Write to local clone — use GENERATED-README.md to avoid overwriting original
    readme_path = os.path.join(session["clone_dir"], "GENERATED-README.md")
    with open(readme_path, "w") as f:
        f.write(readme_content)

    console.print(f"\n  [bold green]GENERATED-README.md written to {readme_path}[/bold green]\n")
    console.print(Panel(readme_content[:800] + ("..." if len(readme_content) > 800 else ""), title="Preview", border_style="green"))

    # Optional GitHub push
    push = console.input("\n  Push to your GitHub? (y/n)> ").strip().lower()
    if push == "y":
        push_to_github()
    else:
        console.print(f"  [dim]Skipped. Your README is at {readme_path}[/dim]")


def push_to_github():
    """Push the generated README to GitHub — either existing repo or new one."""
    repo_name = session["repo_name"]
    clone_dir = session["clone_dir"]

    # Check if clone already has a remote (i.e., cloned from a real repo)
    remote_result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=clone_dir, capture_output=True, text=True,
    )
    has_remote = remote_result.returncode == 0
    remote_url = remote_result.stdout.strip() if has_remote else ""

    # Get current GitHub user
    user_result = subprocess.run(
        ["gh", "api", "user", "-q", ".login"],
        capture_output=True, text=True,
    )
    gh_user = user_result.stdout.strip()

    # Determine if this is the user's own repo
    is_own_repo = has_remote and gh_user.lower() in remote_url.lower()

    if is_own_repo:
        console.print(f"  [dim]Detected: this is your repo ({remote_url})[/dim]")
        console.print(f"  Push GENERATED-README.md to {remote_url}? (y/n)")
        if console.input("  > ").strip().lower() != "y":
            return
    elif has_remote:
        console.print(f"  [dim]Cloned from: {remote_url}[/dim]")
        console.print(f"  This is someone else's repo. Options:")
        console.print(f"    1) Create a fork under {gh_user} and push there")
        console.print(f"    2) Create a new repo ({gh_user}/{repo_name}) and push")
        console.print(f"    3) Cancel")
        choice = console.input("  > ").strip()
        if choice == "3":
            return
        elif choice == "1":
            # Fork the repo
            with Live(Spinner("dots", text="  Forking..."), console=console, refresh_per_second=10):
                try:
                    subprocess.run(
                        ["gh", "repo", "fork", remote_url, "--clone=false"],
                        check=True, capture_output=True, text=True,
                    )
                    subprocess.run(
                        ["git", "remote", "set-url", "origin", f"https://github.com/{gh_user}/{repo_name}.git"],
                        cwd=clone_dir, capture_output=True,
                    )
                except subprocess.CalledProcessError as e:
                    console.print(f"  [red]Fork failed: {e.stderr.strip() if e.stderr else e}[/red]")
                    return
        elif choice == "2":
            # Remove old remote and create fresh
            subprocess.run(["git", "remote", "remove", "origin"], cwd=clone_dir, capture_output=True)
            has_remote = False
        else:
            return

    with Live(Spinner("dots", text="  Pushing to GitHub..."), console=console, refresh_per_second=10):
        try:
            if has_remote or is_own_repo:
                # Commit and push to existing remote
                subprocess.run(["git", "add", "GENERATED-README.md"], cwd=clone_dir, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", "Add AI-generated README"],
                    cwd=clone_dir, capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "push"],
                    cwd=clone_dir, check=True, capture_output=True, text=True,
                )
            else:
                # Create new repo and push
                subprocess.run(["git", "add", "GENERATED-README.md"], cwd=clone_dir, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", "Add AI-generated README"],
                    cwd=clone_dir, capture_output=True, text=True,
                )
                subprocess.run(
                    ["gh", "repo", "create", repo_name, "--public", "--source", clone_dir, "--push"],
                    check=True, capture_output=True, text=True,
                )
        except subprocess.CalledProcessError as e:
            console.print(f"  [red]GitHub push failed: {e.stderr.strip() if e.stderr else e}[/red]")
            return

    console.print(f"  [green]Pushed to https://github.com/{gh_user}/{repo_name}[/green]")


# --- Menu & History ---

def show_menu():
    """Display the main menu."""
    cached = f"(last scan: {session['repo_name']} — {len(session['scan_data']['files'])} files)" if session["scan_data"] else ""

    existing = list_existing_projects()
    existing_label = f"  3) Load existing project   ({len(existing)} found)" if existing else "  3) Load existing project   [dim](none found)[/dim]"

    menu = (
        "  1) Generate README from a URL\n"
        "  2) Re-run on last scanned repo\n"
        f"{existing_label}\n"
        "  h) Session history\n"
        "  q) Quit"
    )

    console.print()
    console.print(Panel(menu, title="README Generator CLI", border_style="cyan"))
    if cached:
        console.print(f"  [dim]{cached}[/dim]")


def show_history():
    """Display session history of agent calls."""
    if not session["history"]:
        console.print("  [dim]No history yet.[/dim]")
        return

    table = Table(title="Session History")
    table.add_column("Time", style="dim")
    table.add_column("Agent", style="cyan")
    table.add_column("Output Preview")

    for entry in session["history"]:
        table.add_row(entry["time"], entry["agent"], entry["output_preview"])

    console.print(table)


# --- Main ---

def main():
    console.print(Panel(
        "[bold]README Generator CLI[/bold]\n"
        "Generate README files for any public GitHub repo using AI agents",
        border_style="cyan",
    ))

    check_prerequisites()

    while True:
        show_menu()
        choice = console.input("\n  choice> ").strip().lower()

        if choice == "1":
            session["scan_data"] = None  # Clear cache for new URL
            generate_readme()

        elif choice == "2":
            if not session["scan_data"]:
                console.print("  [red]No previous scan. Use option 1 or 3 first.[/red]")
            else:
                generate_readme()

        elif choice == "3":
            load_existing_project()
            if session["scan_data"]:
                console.print("  [dim]Project loaded. Use option 2 to generate README.[/dim]")

        elif choice == "h":
            show_history()

        elif choice == "q":
            console.print("  [dim]Goodbye![/dim]")
            break

        else:
            console.print("  [red]Invalid choice. Enter 1, 2, 3, h, or q.[/red]")


if __name__ == "__main__":
    main()
