variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token — needs Tunnel:Edit, DNS:Edit, Zone:Read"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (found on any zone's overview page)"
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Zone ID for roastandresolve.com (Cloudflare dashboard → domain overview)"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo in owner/name format — scopes the OIDC role"
  type        = string
  default     = "stevenfackley/undertow-engine"
}

variable "create_github_oidc_provider" {
  description = <<-EOT
    Set to false if another project (e.g. roast-and-resolve) already created the
    GitHub Actions OIDC provider in this AWS account. Each account can only have
    one OIDC provider per IdP URL.
  EOT
  type        = bool
  default     = true
}
