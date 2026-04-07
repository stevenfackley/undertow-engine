resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_tunnel" "undertow" {
  account_id = var.cloudflare_account_id
  name       = "undertow-engine"
  secret     = random_id.tunnel_secret.b64_std
}

resource "cloudflare_tunnel_config" "undertow" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_tunnel.undertow.id

  config {
    ingress_rule {
      hostname = "undertow.roastandresolve.com"
      service  = "http://undertow-api:8001"
    }
    # Required catch-all — cloudflared rejects configs without one
    ingress_rule {
      service = "http_status:404"
    }
  }
}

resource "cloudflare_record" "undertow" {
  zone_id = var.cloudflare_zone_id
  name    = "undertow"
  value   = "${cloudflare_tunnel.undertow.id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
}
