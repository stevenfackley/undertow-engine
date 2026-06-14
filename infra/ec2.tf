# ---------------------------------------------------------------------------
# ec2.tf — Dedicated prod host for undertow.
#
# Runs the full docker-compose stack (api + worker + beat + redis + flower +
# cloudflared). The deploy pipeline (deploy-prod.yml) reaches it via SSM Run
# Command; the public is served only through the Cloudflare tunnel
# (undertow.roastandresolve.com -> cloudflared -> undertow-api:8001).
#
# Design:
#   - No inbound. cloudflared and the SSM agent both dial OUT; nothing listens
#     publicly. Security group is egress-only.
#   - SSM-managed, no SSH key. Access and deploys go through SSM.
#   - Builds the image on-box from source (compose `--build`), so the instance
#     needs no ECR pull rights — only AmazonSSMManagedInstanceCore.
#   - Tagged App=undertow so deploy-prod.yml can target it deterministically
#     instead of grabbing the first online SSM instance in the account.
# ---------------------------------------------------------------------------

# Latest Ubuntu 24.04 LTS (Canonical) amd64.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

# Default (public) subnet in the first AZ of the region.
data "aws_subnet" "prod" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.aws_region}a"
  default_for_az    = true
}

# --- Instance role: lets SSM Run Command reach the box for deploys ----------
resource "aws_iam_role" "instance" {
  name = "undertow-prod-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "undertow-prod-instance"
  role = aws_iam_role.instance.name
}

# --- Security group: zero inbound, all outbound ----------------------------
resource "aws_security_group" "instance" {
  name        = "undertow-prod"
  description = "undertow prod host - egress only; reachable via Cloudflare tunnel + SSM"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "all outbound (Cloudflare tunnel, SSM, ECR, git, pip, apt)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "undertow-prod"
    App  = "undertow"
  }
}

# --- The host --------------------------------------------------------------
resource "aws_instance" "prod" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = "t3.large"
  subnet_id                   = data.aws_subnet.prod.id
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  associate_public_ip_address = true

  root_block_device {
    volume_type = "gp3"
    volume_size = 40
    encrypted   = true
  }

  # Require IMDSv2.
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  # Install docker + compose + git and clone the repo. The .env (secrets) is
  # created out-of-band via a one-time SSM bootstrap, never baked into TF state
  # or the AMI.
  user_data = <<-EOF
    #!/bin/bash
    set -euxo pipefail

    # SSM agent ships preinstalled on Ubuntu AWS AMIs (snap); make sure it runs.
    snap install amazon-ssm-agent --classic || true
    systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl git

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker

    install -d -m 0755 /opt
    cd /opt
    git clone https://github.com/stevenfackley/undertow-engine.git || true
  EOF

  tags = {
    Name = "undertow-prod"
    App  = "undertow"
  }

  # Don't recreate the box when a newer Ubuntu AMI is published or user_data is
  # tweaked — it's a long-lived pet, redeployed in place via SSM.
  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

output "prod_instance_id" {
  description = "EC2 instance ID — set as GitHub secret PROD_EC2_INSTANCE_ID (deploy fallback)"
  value       = aws_instance.prod.id
}

output "prod_public_ip" {
  description = "Public IP (egress / reference only — nothing listens inbound)"
  value       = aws_instance.prod.public_ip
}
