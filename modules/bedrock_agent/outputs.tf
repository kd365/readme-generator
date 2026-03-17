output "agent_id" {
  description = "The ID of the created Bedrock Agent."
  value       = aws_bedrockagent_agent.this.agent_id
}

output "agent_name" {
  description = "The name of the created Bedrock Agent."
  value       = aws_bedrockagent_agent.this.agent_name
}

output "agent_arn" {
  description = "The ARN of the created Bedrock Agent."
  value       = aws_bedrockagent_agent.this.agent_arn
}