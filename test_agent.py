import boto3
import uuid
import sys

client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

AGENTS = {
    "scanner":    "43NRA4J2G8",
    "summarizer": "WJBSERSMYZ",
    "install":    "JDEFJPD6DC",
    "usage":      "MU9XVMK8LN",
}

SAMPLE_INPUT = '{"files": [".gitignore", "README.md", "lambda_function.py", "requirements.txt"]}'


def invoke_agent(agent_id, input_text):
    response = client.invoke_agent(
        agentId=agent_id,
        agentAliasId="TSTALIASID",
        sessionId=str(uuid.uuid4()),
        inputText=input_text,
    )
    result = ""
    for event in response["completion"]:
        if "chunk" in event:
            result += event["chunk"]["bytes"].decode("utf-8")
    return result


if __name__ == "__main__":
    # Usage: python test_agent.py [agent_name]
    # Examples:
    #   python test_agent.py scanner       (tests with repo URL)
    #   python test_agent.py summarizer    (tests with sample file list)
    #   python test_agent.py install
    #   python test_agent.py usage
    #   python test_agent.py all           (tests all analytical agents)

    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target == "scanner":
        url = sys.argv[2] if len(sys.argv) > 2 else "https://github.com/TruLie13/municipal-ai"
        print(f"--- Scanner Agent ---")
        print(f"Input: {url}\n")
        print(invoke_agent(AGENTS["scanner"], f"Scan this repo: {url}"))

    elif target == "all":
        for name in ["summarizer", "install", "usage"]:
            print(f"\n--- {name.title()} Agent ---")
            print(f"Input: {SAMPLE_INPUT}\n")
            print(invoke_agent(AGENTS[name], SAMPLE_INPUT))
            print("-" * 50)

    elif target in AGENTS:
        input_text = SAMPLE_INPUT
        if target == "scanner":
            input_text = "Scan this repo: https://github.com/TruLie13/municipal-ai"
        print(f"--- {target.title()} Agent ---")
        print(f"Input: {input_text}\n")
        print(invoke_agent(AGENTS[target], input_text))

    else:
        print(f"Unknown agent: {target}")
        print(f"Available: {', '.join(AGENTS.keys())}, all")
