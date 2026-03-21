# src/repo_scanner/lambda_function.py
import json
import os
import subprocess
import shutil

# Key files that downstream agents need to generate a good README
KEY_FILES = [
    "README.md", "readme.md", "README.rst",
    "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
    "package.json", "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "LICENSE", "CONTRIBUTING.md",
]

# Max bytes to read per file to stay within Lambda response limits
MAX_FILE_SIZE = 5000


def read_key_files(repo_dir, file_list):
    """Read contents of key project files for downstream agents."""
    contents = {}
    for f in file_list:
        basename = os.path.basename(f)
        if basename in KEY_FILES or f in KEY_FILES:
            full_path = os.path.join(repo_dir, f)
            try:
                size = os.path.getsize(full_path)
                if size <= MAX_FILE_SIZE:
                    with open(full_path, "r", errors="replace") as fh:
                        contents[f] = fh.read()
                else:
                    contents[f] = f"[truncated — {size} bytes, showing first {MAX_FILE_SIZE}]"
                    with open(full_path, "r", errors="replace") as fh:
                        contents[f] = fh.read(MAX_FILE_SIZE) + "\n... [truncated]"
            except Exception:
                pass
    return contents


def get_disk_usage(path):
    """Get disk usage of a directory in bytes."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def list_files_in_repo(repo_url, context=None):
    """Clones a git repo and returns a list of its files plus key file contents."""
    repo_dir = "/tmp/repo"
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)

    # Check available space before cloning
    tmp_stat = os.statvfs("/tmp")
    available_mb = (tmp_stat.f_bavail * tmp_stat.f_frsize) / (1024 * 1024)
    print(f"Available /tmp space: {available_mb:.0f} MB")

    try:
        print(f"Cloning repository: {repo_url}")
        # Get remaining time if context is available
        timeout_sec = 80  # default safety margin under 90s Lambda timeout
        if context:
            remaining_ms = context.get_remaining_time_in_millis()
            timeout_sec = max(10, (remaining_ms // 1000) - 10)
            print(f"Time remaining: {remaining_ms}ms, clone timeout: {timeout_sec}s")

        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, repo_dir],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        print("Repository cloned successfully.")

        # Check how much space the clone used
        used_mb = get_disk_usage(repo_dir) / (1024 * 1024)
        print(f"Clone size: {used_mb:.1f} MB")

        file_list = []
        for root, dirs, files in os.walk(repo_dir):
            if '.git' in dirs:
                dirs.remove('.git')
            for name in files:
                relative_path = os.path.relpath(os.path.join(root, name), repo_dir)
                file_list.append(relative_path)

        key_contents = read_key_files(repo_dir, file_list)
        return {"files": file_list, "key_file_contents": key_contents}
    except subprocess.TimeoutExpired:
        print(f"ERROR: Clone timed out after {timeout_sec}s")
        return {"error": f"Repository is too large to clone within the time limit ({timeout_sec}s). Try a smaller repository.", "files": []}
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        print(f"Git clone failed with stderr: {stderr}")
        if "not found" in stderr.lower() or "repository" in stderr.lower():
            return {"error": "Repository not found or not accessible. Ensure the URL is correct and the repo is public.", "files": []}
        if "authentication" in stderr.lower() or "permission" in stderr.lower():
            return {"error": "Repository requires authentication. Only public repositories are supported.", "files": []}
        return {"error": f"Git clone failed: {stderr.strip()}", "files": []}
    except OSError as e:
        if e.errno == 28:  # No space left on device
            return {"error": "Repository is too large — exceeded the 1 GB storage limit. Try a smaller repository.", "files": []}
        print(f"OS error: {e}")
        return {"error": str(e), "files": []}
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return {"error": str(e), "files": []}


def handler(event, context):
    """The main Lambda handler function."""
    print(f"--- REPO SCANNER LAMBDA RUNNING ---")
    print(f"Full event received: {json.dumps(event)}")

    repo_url = None
    try:
        properties = event['requestBody']['content']['application/json']['properties']
        repo_url = next((prop['value'] for prop in properties if prop['name'] == 'repo_url'), None)
    except (KeyError, StopIteration):
        print("Error: Could not find repo_url in the expected path.")

    if not repo_url:
        print("Error: repo_url is missing.")
        result = {"error": "repo_url parameter is required", "files": []}
    else:
        result = list_files_in_repo(repo_url, context)

    # Truncate result to stay within Bedrock Agent context limits
    MAX_RESPONSE_SIZE = 20000
    response_body = json.dumps(result)
    if len(response_body) > MAX_RESPONSE_SIZE:
        print(f"Response too large ({len(response_body)} chars), truncating...")
        # Keep priority files, trim the rest
        trimmed = {"files": result.get("files", []), "key_file_contents": {}}
        priority = ["package.json", "requirements.txt", "setup.py", "pyproject.toml",
                     "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
                     "Dockerfile", "docker-compose.yml", ".env.example", "Makefile"]
        kfc = result.get("key_file_contents", {})
        # Priority files first
        for k, v in kfc.items():
            if os.path.basename(k) in priority:
                trimmed["key_file_contents"][k] = v[:3000] if len(v) > 3000 else v
        # Then remaining files if space allows
        for k, v in kfc.items():
            if os.path.basename(k) not in priority:
                trimmed["key_file_contents"][k] = v[:2000] if len(v) > 2000 else v
                if len(json.dumps(trimmed)) > MAX_RESPONSE_SIZE:
                    break
        # If still too large, truncate file list to just names
        response_body = json.dumps(trimmed)
        if len(response_body) > MAX_RESPONSE_SIZE:
            trimmed["files"] = trimmed["files"][:500]
            response_body = json.dumps(trimmed)
        print(f"Truncated to {len(response_body)} chars, {len(trimmed['key_file_contents'])} key files")

    api_response = {
        'messageVersion': '1.0',
        'response': {
            'actionGroup': event['actionGroup'],
            'apiPath': event['apiPath'],
            'httpMethod': event['httpMethod'],
            'httpStatusCode': 200,
            'responseBody': {
                'application/json': {
                    'body': response_body
                }
            }
        }
    }

    return api_response
