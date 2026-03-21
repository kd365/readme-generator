# src/save_to_s3/lambda_function.py
"""Lambda that saves the final README content to S3."""

import json
import boto3
import os

s3_client = boto3.client('s3')

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")


def handler(event, context):
    """Save compiled README to S3."""
    print(f"Event: {json.dumps(event)[:500]}")

    repo_name = event.get("repo_name", "unknown")
    readme_content = event.get("readme_content", "")
    output_key = f"outputs/{repo_name}/README.md"

    # Strip preamble before first # header
    lines = readme_content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            readme_content = "\n".join(lines[i:])
            break

    try:
        s3_client.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=output_key,
            Body=readme_content,
            ContentType='text/markdown'
        )
        print(f"Saved to s3://{OUTPUT_BUCKET}/{output_key}")
        return {
            "status": "success",
            "bucket": OUTPUT_BUCKET,
            "key": output_key
        }
    except Exception as e:
        print(f"Error saving to S3: {e}")
        return {
            "status": "error",
            "error": str(e)
        }
