output "ecr_repository_url" {
  description = "ECR repo URL — use this in deploy workflows"
  value       = aws_ecr_repository.undertow.repository_url
}

output "github_actions_role_arn" {
  description = "Copy this to GitHub secret AWS_OIDC_ROLE_ARN"
  value       = aws_iam_role.github_actions.arn
}

output "tunnel_token" {
  description = "Copy this to server .env as TUNNEL_TOKEN and GitHub secret TUNNEL_TOKEN"
  value       = cloudflare_tunnel.undertow.tunnel_token
  sensitive   = true
}

output "tunnel_id" {
  description = "Cloudflare tunnel ID"
  value       = cloudflare_tunnel.undertow.id
}
