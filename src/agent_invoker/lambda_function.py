# src/agent_invoker/lambda_function.py
"""Generic Lambda that invokes a Bedrock agent. Used by Step Functions.
The agent ID and alias are passed in the event payload.
Includes intelligent truncation for large inputs."""

import json
import boto3
import os
import uuid

bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')

MAX_INPUT_SIZE = 25000

# Dependency files get priority when truncating — these tell agents
# what language, package manager, and setup the project uses
PRIORITY_FILES = [
    "package.json", "requirements.txt", "setup.py", "pyproject.toml",
    "Cargo.toml", "go.mod", "Gemfile", "pom.xml", "build.gradle",
    "Dockerfile", "docker-compose.yml", ".env.example", "Makefile",
]


def truncate_input(input_text):
    """Intelligently truncate large inputs, preserving priority file contents."""
    if len(input_text) <= MAX_INPUT_SIZE:
        return input_text

    # Try to parse as JSON (scan data from the scanner agent)
    try:
        data = json.loads(input_text)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — just truncate raw text
        print(f"Truncating raw text from {len(input_text)} to {MAX_INPUT_SIZE}")
        return input_text[:MAX_INPUT_SIZE]

    # If it has files + key_file_contents structure, truncate intelligently
    if "files" in data and "key_file_contents" in data:
        trimmed = {"files": data["files"], "key_file_contents": {}}

        # Add priority files first
        for k, v in data["key_file_contents"].items():
            basename = os.path.basename(k)
            if basename in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:3000] if len(v) > 3000 else v

        # Then add remaining files if space allows
        for k, v in data["key_file_contents"].items():
            if os.path.basename(k) not in PRIORITY_FILES:
                trimmed["key_file_contents"][k] = v[:2000] if len(v) > 2000 else v
                if len(json.dumps(trimmed)) > MAX_INPUT_SIZE:
                    break

        result = json.dumps(trimmed)
        print(f"Truncated scan data: {len(input_text)} -> {len(result)} chars, "
              f"kept {len(trimmed['key_file_contents'])} key files")
        return result

    # Generic JSON — just stringify and truncate
    result = json.dumps(data)
    if len(result) > MAX_INPUT_SIZE:
        print(f"Truncating JSON from {len(result)} to {MAX_INPUT_SIZE}")
        return result[:MAX_INPUT_SIZE]
    return result


def invoke_agent(agent_id, alias_id, input_text):
    """Invoke a Bedrock agent and return the text response."""
    input_text = truncate_input(input_text)
    print(f"Invoking agent {agent_id} with input length: {len(input_text)}")
    response = bedrock_agent_runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=str(uuid.uuid4()),
        inputText=input_text
    )
    completion = ""
    for event in response.get("completion", []):
        if "chunk" in event:
            completion += event["chunk"]["bytes"].decode("utf-8")
    print(f"Agent {agent_id} returned {len(completion)} chars")
    return completion


def handler(event, context):
    """Generic handler — reads agent_id, alias_id, and input_text from event."""
    print(f"Event: {json.dumps(event)[:500]}")

    agent_id = event.get("agent_id")
    alias_id = event.get("alias_id", "TSTALIASID")
    input_text = event.get("input_text", "")
    step_name = event.get("step_name", "unknown")

    if not agent_id:
        return {"error": "Missing agent_id", "step_name": step_name}

    try:
        result = invoke_agent(agent_id, alias_id, input_text)
        return {
            "step_name": step_name,
            "result": result
        }
    except Exception as e:
        print(f"Error invoking agent: {e}")
        return {
            "step_name": step_name,
            "error": str(e),
            "result": "This section could not be generated."
        }
