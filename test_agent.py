import boto3
import uuid

client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

response = client.invoke_agent(
    agentId="43NRA4J2G8",
    agentAliasId="TSTALIASID",
    sessionId=str(uuid.uuid4()),
    inputText="Scan this repo: https://github.com/TruLie13/municipal-ai"
)

result = ""
for event in response["completion"]:
    if "chunk" in event:
        result += event["chunk"]["bytes"].decode("utf-8")

print(result)
