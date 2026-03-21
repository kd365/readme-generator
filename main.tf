// terraform configuration for the README Generator project, including all resources for Labs 1-4 and the CI/CD pipeline setup.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "name_suffix" {
  description = "Suffix to append to resource names to avoid collisions in shared accounts."
  type        = string
  default     = "KH"
}

resource "random_string" "suffix" {
  length  = 8
  special = false
  upper   = false
}

module "s3_bucket" {
  source      = "./modules/s3"
  bucket_name = "readme-generator-output-bucket-${random_string.suffix.result}"
}


# Role specifically for the Lambda function to run
module "lambda_execution_role" {
  source             = "./modules/iam"
  role_name          = "ReadmeGeneratorLambdaExecutionRole-${var.name_suffix}"
  service_principals = ["lambda.amazonaws.com"]
  policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
}

resource "aws_iam_policy" "repo_scanner_s3_write" {
  name = "RepoScannerS3WritePolicy-${var.name_suffix}"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action   = ["s3:PutObject"]
      Effect   = "Allow"
      Resource = "${module.s3_bucket.bucket_arn}/scans/*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "repo_scanner_s3_write_attach" {
  role       = module.lambda_execution_role.role_name
  policy_arn = aws_iam_policy.repo_scanner_s3_write.arn
}



# Role specifically for the Bedrock Agent to use
module "bedrock_agent_role" {
  source             = "./modules/iam"
  role_name          = "ReadmeGeneratorBedrockAgentRole-${var.name_suffix}"
  service_principals = ["bedrock.amazonaws.com"]
  policy_arns = [
    "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
  ]
}

output "readme_bucket_name" {
  description = "The name of the S3 bucket where README files are stored."
  value       = module.s3_bucket.bucket_id
}

data "archive_file" "repo_scanner_zip" {
  type        = "zip"
  source_dir  = "${path.root}/src/repo_scanner"
  output_path = "${path.root}/dist/repo_scanner.zip"
}

resource "aws_lambda_function" "repo_scanner_lambda" {
  function_name    = "RepoScannerTool-${var.name_suffix}"
  role             = module.lambda_execution_role.role_arn # Uses the dedicated Lambda role
  filename         = data.archive_file.repo_scanner_zip.output_path
  handler          = "lambda_function.handler"
  runtime          = "python3.11"
  timeout          = 90
  source_code_hash = data.archive_file.repo_scanner_zip.output_base64sha256

  ephemeral_storage {
    size = 1024 # 1 GB for cloning larger repos
  }

  layers = ["arn:aws:lambda:us-east-1:553035198032:layer:git-lambda2:8"]

  environment {
    variables = {
      OUTPUT_BUCKET = module.s3_bucket.bucket_id
    }
  }
}

module "repo_scanner_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Repo_Scanner_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction             = "You are the Repo Scanner Agent. When given a GitHub URL, immediately use the scan_repo tool to clone it and return the complete file listing along with key file contents. Do not attempt to answer questions about the repository without first scanning it. Always return the full results from the tool."
}

resource "aws_bedrockagent_agent_action_group" "repo_scanner_action_group" {
  agent_id            = module.repo_scanner_agent.agent_id
  agent_version       = "DRAFT"
  action_group_name   = "ScanRepoAction"
  action_group_state  = "ENABLED"

  action_group_executor {
    lambda = aws_lambda_function.repo_scanner_lambda.arn
  }

  api_schema {
    payload = file("${path.root}/repo_scanner_schema.json")
  }
}

# This resource grants the Bedrock Agent permission to invoke our Lambda function
resource "aws_lambda_permission" "allow_bedrock_to_invoke_lambda" {
  statement_id  = "AllowBedrockToInvokeRepoScannerLambda"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.repo_scanner_lambda.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = module.repo_scanner_agent.agent_arn
}

# --- Lab 3: Analytical Agents ---

module "project_summarizer_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Project_Summarizer_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are an expert software developer writing a project summary for a README.md.
    You will receive a JSON object containing a file list and key file contents from a repository.
    Analyze both the filenames AND the file contents to write a confident, factual summary of the project's purpose, architecture, and key components.
    Write as if you are the project author describing your own project. Do not use uncertain or hedging language like 'it appears to be,' 'likely,' or 'seems to be.'
    Your response must be only the summary paragraph. No preamble, no headers.
    If the input does not appear to be repository data, respond with: 'Please provide a repository file list and contents.'
  EOT
}


module "installation_guide_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Installation_Guide_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a technical writer creating a README.md. You will receive a JSON object with "files" (list of filenames) and "key_file_contents" (actual file contents).
    Write a '## Getting Started' section with accurate installation instructions.

    CRITICAL RULES:
    - ONLY write instructions for ecosystems that have a CONFIRMED dependency file in the "files" list.
    - If package.json exists but requirements.txt/setup.py/pyproject.toml do NOT exist, this is NOT a Python project. Do NOT include pip commands.
    - If requirements.txt exists but package.json does NOT exist, this is NOT a Node.js project. Do NOT include npm/pnpm commands.
    - Read the CONTENTS of dependency files from "key_file_contents" to determine exact versions, prerequisites, and commands.
    - Include prerequisites, installation commands in bash code blocks, and environment setup if .env.example is present.
    - Do NOT fabricate or guess about tools/ecosystems not evidenced in the file list.

    Your response must contain ONLY the ## Getting Started section. No preamble or extra commentary.
    If you do not see any recognizable dependency files, respond with: 'No dependency management file found.'
    If the input does not appear to be repository data, respond with: 'Please provide a repository file list and contents.'
  EOT
}

module "usage_examples_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Usage_Examples_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a software developer writing a README.md. You will receive a JSON object containing a file list and key file contents from a repository.
    Analyze both filenames AND file contents to write a '## Usage' section in Markdown.
    Focus ONLY on how to USE the project after it is already installed. Do NOT repeat installation steps, dependency installation, or setup instructions — those belong in a separate Getting Started section.
    Cover: how to run the project, key CLI commands, configuration options, and practical examples.
    If a README or documentation file is included in the data, use it to provide accurate usage examples rather than guessing.
    Show commands in bash code blocks.
    Your response must contain ONLY the ## Usage section. No preamble or extra commentary.
    If the input does not appear to be repository data, respond with: 'Please provide a repository file list and contents.'
  EOT
}

# --- Lab 4: Final Compiler Agent & Orchestrator ---


module "final_compiler_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Final_Compiler_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a technical document compiler. You will receive a JSON object with keys: repository_name, project_summary, installation_guide, and usage_examples.
    Assemble them into a single, clean Markdown document with this exact structure:

    # {repository_name}

    ## Project Summary
    {project_summary content}

    ## Getting Started
    {installation_guide content}

    ## Usage
    {usage_examples content}

    Rules:
    - DEDUPLICATE: If installation instructions appear in both Getting Started and Usage, keep them ONLY in Getting Started. Remove duplicates from Usage.
    - Remove any duplicate section headers (e.g., if Usage content already starts with ## Usage, do not add another).
    - Do not add sections that were not provided.
    - Do not add preamble, commentary, explanations of your process, or conversational text.
    - If a section contains an error message or is empty, include a placeholder: 'This section could not be generated.'
    - Return ONLY the pure Markdown document starting with the # header.
  EOT
}

# DEDICATED role for the Orchestrator Lambda
module "orchestrator_execution_role" {
  source             = "./modules/iam"
  role_name          = "ReadmeGeneratorOrchestratorExecutionRole-${var.name_suffix}"
  service_principals = ["lambda.amazonaws.com"]
  policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  ]
}

# Orchestrator-specific permissions policy
resource "aws_iam_policy" "orchestrator_permissions" {
  name        = "ReadmeGeneratorOrchestratorPolicy-${var.name_suffix}"
  description = "Allows Lambda to invoke Bedrock Agents and use the S3 bucket."

  lifecycle {
    ignore_changes = [policy]
  }

  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Sid    = "BedrockAgentInvoke"
        Action = [
          "bedrock:InvokeAgent",
          "bedrock-agent-runtime:InvokeAgent"
        ]
        Effect   = "Allow"
        Resource = "*"
      },
      {
        Sid    = "S3BucketOperations"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:HeadObject"
        ]
        Effect   = "Allow"
        Resource = "${module.s3_bucket.bucket_arn}/*"
      }
    ]
  })
}

# Attach the policy to the ORCHESTRATOR role
resource "aws_iam_role_policy_attachment" "orchestrator_permissions_attach" {
  role       = module.orchestrator_execution_role.role_name
  policy_arn = aws_iam_policy.orchestrator_permissions.arn
}

# Package and deploy orchestrator Lambda
data "archive_file" "orchestrator_zip" {
  type        = "zip"
  source_dir  = "${path.root}/src/orchestrator"
  output_path = "${path.root}/dist/orchestrator.zip"
}

resource "aws_lambda_function" "orchestrator_lambda" {
  function_name    = "ReadmeGeneratorOrchestrator-${var.name_suffix}"
  role             = module.orchestrator_execution_role.role_arn
  filename         = data.archive_file.orchestrator_zip.output_path
  handler          = "lambda_function.handler"
  runtime          = "python3.11"
  timeout          = 180
  source_code_hash = data.archive_file.orchestrator_zip.output_base64sha256

  environment {
    variables = {
      REPO_SCANNER_AGENT_ID             = module.repo_scanner_agent.agent_id
      REPO_SCANNER_AGENT_ALIAS_ID       = "TSTALIASID"
      PROJECT_SUMMARIZER_AGENT_ID       = module.project_summarizer_agent.agent_id
      PROJECT_SUMMARIZER_AGENT_ALIAS_ID = "TSTALIASID"
      INSTALLATION_GUIDE_AGENT_ID       = module.installation_guide_agent.agent_id
      INSTALLATION_GUIDE_AGENT_ALIAS_ID = "TSTALIASID"
      USAGE_EXAMPLES_AGENT_ID           = module.usage_examples_agent.agent_id
      USAGE_EXAMPLES_AGENT_ALIAS_ID     = "TSTALIASID"
      FINAL_COMPILER_AGENT_ID           = module.final_compiler_agent.agent_id
      FINAL_COMPILER_AGENT_ALIAS_ID     = "TSTALIASID"
      OUTPUT_BUCKET                     = module.s3_bucket.bucket_id
    }
  }
}

# S3 trigger configuration
resource "aws_lambda_permission" "allow_s3_to_invoke_orchestrator" {
  statement_id  = "AllowS3ToInvokeOrchestratorLambda"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = module.s3_bucket.bucket_arn
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = module.s3_bucket.bucket_id

  lambda_function {
    lambda_function_arn = aws_lambda_function.orchestrator_lambda.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "inputs/"
  }

  depends_on = [aws_lambda_permission.allow_s3_to_invoke_orchestrator]
}

# --- NEW RESOURCES FOR CI/CD PIPELINE ---

resource "random_string" "state_bucket_suffix" {
  length  = 8
  special = false
  upper   = false
}

resource "aws_s3_bucket" "terraform_state" {
  bucket = "tf-readme-generator-state-${random_string.state_bucket_suffix.result}"
}

resource "aws_dynamodb_table" "terraform_locks" {
  name         = "readme-generator-tf-locks-${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

output "terraform_state_bucket_name" {
  description = "The name of the S3 bucket for the Terraform state."
  value       = aws_s3_bucket.terraform_state.bucket
}

# GitHub Actions OIDC role
resource "aws_iam_role" "github_actions_role" {
  name = "GitHubActionsRole-ReadmeGenerator-${var.name_suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = "arn:aws:iam::388691194728:oidc-provider/token.actions.githubusercontent.com"
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:sub" = "repo:kd365/readme-generator:ref:refs/heads/main"
        }
      }
    }]
  })


}


resource "aws_iam_role_policy_attachment" "github_actions_permissions" {
  role       = aws_iam_role.github_actions_role.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "github_actions_role_arn" {
  description = "The ARN of the IAM role for GitHub Actions."
  value       = aws_iam_role.github_actions_role.arn
}