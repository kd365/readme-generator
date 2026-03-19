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
    Analyze the provided file list and write a confident, factual summary of the project's purpose and key components.
    **Do not use uncertain or hedging language** like 'it appears to be,' 'likely,' or 'seems to be.' State your analysis as fact.
    Your response must be only the summary paragraph.
  EOT
}


module "installation_guide_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Installation_Guide_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a technical writer creating a README.md. Your ONLY job is to scan the provided list of filenames.
    If you see a common dependency file, write a '## Installation' section in Markdown.
    Your response must be concise and contain ONLY the command.
    For example, if you see 'requirements.txt', your entire response MUST be:
    ## Installation
    `
    `
    `bash
    pip install -r requirements.txt
    `
    `
    `
    If you do not see any recognizable dependency files, respond with an empty string.
  EOT
}

module "usage_examples_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Usage_Examples_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a software developer writing a README.md. Your ONLY task is to identify the most likely entry point from a list of filenames.
    Write a '## Usage' section in Markdown showing the command to run the project.
    Your response MUST be concise and wrap the command in a bash code block.
    For example, if you see 'main.py', your entire response MUST be:
    ## Usage
    `
    `
    `bash
    python main.py
    `
    `
    `
  EOT
}

# --- Lab 4: Final Compiler Agent & Orchestrator ---


module "final_compiler_agent" {
  source                  = "./modules/bedrock_agent"
  agent_name              = "Final_Compiler_Agent-${var.name_suffix}"
  agent_resource_role_arn = module.bedrock_agent_role.role_arn
  instruction = <<-EOT
    You are a technical document compiler. Your task is to take a JSON object containing different sections of a README file and assemble them into a single Markdown document.
    Use the repository name for the main H1 header (e.g., # repository_name).
    Combine the other sections provided.
    Your output MUST be only the pure, complete Markdown document.
    Do NOT include any preamble, apologies, explanations of your process, or any conversational text.
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

  lifecycle {
    ignore_changes = [assume_role_policy]
  }
}


resource "aws_iam_role_policy_attachment" "github_actions_permissions" {
  role       = aws_iam_role.github_actions_role.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "github_actions_role_arn" {
  description = "The ARN of the IAM role for GitHub Actions."
  value       = aws_iam_role.github_actions_role.arn
}