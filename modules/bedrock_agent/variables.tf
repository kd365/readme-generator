variable "agent_name" {
  description = "The name of the Bedrock Agent."
  type        = string
}

variable "foundation_model" {
  description = "The foundation model for the agent."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-20250514-v1:0"
}

variable "instruction" {
  description = "The instruction prompt for the agent."
  type        = string
}

variable "agent_resource_role_arn" {
  description = "The ARN of the IAM role for the agent."
  type        = string
}