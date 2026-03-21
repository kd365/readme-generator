# src/agent_invoker/lambda_function.py
"""Generic Lambda that invokes a Bedrock agent. Used by Step Functions.
The agent ID and alias are passed in the event payload."""

import json
import boto3
import os
import uuid

bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')
s3_client = boto3.client('s3')


def invoke_agent(agent_id, alias_id, input_text):
    """Invoke a Bedrock agent and return the text response."""
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
