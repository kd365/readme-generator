resource "aws_bedrockagent_agent" "this" {
  agent_name              = var.agent_name
  foundation_model        = var.foundation_model
  instruction             = var.instruction
  agent_resource_role_arn = var.agent_resource_role_arn
}