# src/orchestrator/lambda_function.py
import json
import boto3
import os
import urllib.parse

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_agent_runtime_client = boto3.client('bedrock-agent-runtime')

# Get agent details and bucket name from environment variables
REPO_SCANNER_AGENT_ID = os.environ.get("REPO_SCANNER_AGENT_ID")
REPO_SCANNER_AGENT_ALIAS_ID = os.environ.get("REPO_SCANNER_AGENT_ALIAS_ID")
PROJECT_SUMMARIZER_AGENT_ID = os.environ.get("PROJECT_SUMMARIZER_AGENT_ID")
PROJECT_SUMMARIZER_AGENT_ALIAS_ID = os.environ.get("PROJECT_SUMMARIZER_AGENT_ALIAS_ID")
INSTALLATION_GUIDE_AGENT_ID = os.environ.get("INSTALLATION_GUIDE_AGENT_ID")
INSTALLATION_GUIDE_AGENT_ALIAS_ID = os.environ.get("INSTALLATION_GUIDE_AGENT_ALIAS_ID")
USAGE_EXAMPLES_AGENT_ID = os.environ.get("USAGE_EXAMPLES_AGENT_ID")
USAGE_EXAMPLES_AGENT_ALIAS_ID = os.environ.get("USAGE_EXAMPLES_AGENT_ALIAS_ID")
FINAL_COMPILER_AGENT_ID = os.environ.get("FINAL_COMPILER_AGENT_ID")
FINAL_COMPILER_AGENT_ALIAS_ID = os.environ.get("FINAL_COMPILER_AGENT_ALIAS_ID")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")

print(f"[DEBUG] OUTPUT_BUCKET = {OUTPUT_BUCKET}")


def invoke_agent_helper(agent_id, alias_id, session_id, input_text):
    """A helper function to invoke a Bedrock agent and get the final response."""
    print(f"Invoking agent {agent_id} with input: {input_text}")
    try:
        response = bedrock_agent_runtime_client.invoke_agent(
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=session_id,
            inputText=input_text
        )

        completion = ""
        for event in response.get("completion"):
            chunk = event["chunk"]
            completion += chunk["bytes"].decode()

        print(f"Agent {agent_id} returned: {completion}")
        return completion
    except Exception as e:
        print(f"Error invoking agent {agent_id}: {e}")
        return f"Error processing this section: {e}"


def handler(event, context):
    """The main Lambda handler function."""
    print(f"Orchestrator started with event: {json.dumps(event)}")

    # 1. Get the repo URL from the S3 event trigger
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'])

    # Decode the filename: https---github.com-TruLie13-municipal-ai becomes https://github.com/TruLie13/municipal-ai
    # First replace inputs/ prefix, then --- to ://, then the FIRST two hyphens to slashes
    filename = key.replace('inputs/', '')
    repo_url = filename.replace('---', '://', 1)  # Replace only the first ---
    # Replace the next two hyphens with slashes (after ://)
    parts = repo_url.split('://', 1)
    if len(parts) == 2:
        domain_and_path = parts[1]
        # Replace first hyphen with /, second hyphen with /
        domain_and_path = domain_and_path.replace('-', '/', 2)
        repo_url = parts[0] + '://' + domain_and_path

    session_id = context.aws_request_id

    print(f"[DEBUG] Bucket: {bucket}")
    print(f"[DEBUG] Key: {key}")
    print(f"[DEBUG] Repo URL: {repo_url}")
    print(f"[DEBUG] Output Bucket: {OUTPUT_BUCKET}")
    print(f"[DEBUG] Session ID: {session_id}")

    # 2. Extract and sanitize the repo name
    sanitized_repo_name = repo_url.split('/')[-1].replace('.git', '')
    output_key = f"outputs/{sanitized_repo_name}/README.md"

    print(f"[DEBUG] Sanitized repo name: {sanitized_repo_name}")
    print(f"[DEBUG] Output key: {output_key}")

    # Skip the HeadObject check - proceed directly with generation
    print("[DEBUG] Skipping existence check, proceeding with generation...")

    # --- AGENT INVOCATION CHAIN ---

    # 3. Call analytical agents
    print("[DEBUG] Starting agent invocation chain...")
    file_list_json = invoke_agent_helper(
        REPO_SCANNER_AGENT_ID, REPO_SCANNER_AGENT_ALIAS_ID, session_id, repo_url
    )
    project_summary = invoke_agent_helper(
        PROJECT_SUMMARIZER_AGENT_ID, PROJECT_SUMMARIZER_AGENT_ALIAS_ID, session_id, file_list_json
    )
    installation_guide = invoke_agent_helper(
        INSTALLATION_GUIDE_AGENT_ID, INSTALLATION_GUIDE_AGENT_ALIAS_ID, session_id, file_list_json
    )
    usage_examples = invoke_agent_helper(
        USAGE_EXAMPLES_AGENT_ID, USAGE_EXAMPLES_AGENT_ALIAS_ID, session_id, file_list_json
    )

    # 4. Assemble inputs for the Final_Compiler_Agent
    compiler_input = {
        "repository_name": sanitized_repo_name,
        "project_summary": project_summary,
        "installation_guide": installation_guide,
        "usage_examples": usage_examples
    }
    compiler_input_json = json.dumps(compiler_input)

    # 5. Call the Final_Compiler_Agent to get the final Markdown
    readme_content = invoke_agent_helper(
        FINAL_COMPILER_AGENT_ID, FINAL_COMPILER_AGENT_ALIAS_ID, session_id, compiler_input_json
    )

    # 6. Upload the final README.md to the output S3 bucket
    try:
        print(f"[DEBUG] Attempting PutObject to {OUTPUT_BUCKET}/{output_key}")
        s3_client.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=output_key,
            Body=readme_content,
            ContentType='text/markdown'
        )
        print(
            f"Successfully uploaded README.md to s3://{OUTPUT_BUCKET}/{output_key}")
    except Exception as e:
        print(f"Error uploading README.md to S3: {e}")
        raise e

    return {
        'statusCode': 200,
        'body': json.dumps('README.md generated successfully!')
    }
