resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "undertow" {
  account_id = var.cloudflare_account_id
  name       = "undertow-api"
  secret     = random_id.tunnel_secret.b64_std
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "undertow" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.undertow.id

  config {
    ingress_rule {
      hostname = "undertow.roastandresolve.com"
      service  = "http://undertow-api:8001"
    }
    ingress_rule {
      service = "http_status:404"
    }
  }
}

resource "cloudflare_record" "undertow" {
  zone_id = var.cloudflare_zone_id
  name    = "undertow"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.undertow.id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
}
