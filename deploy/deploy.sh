#!/usr/bin/env bash
# Run ON the EC2 instance. Pulls the latest image from ECR and restarts the chatbot container.
# NOTE: legacy path; docker-compose.yml is the supported deploy (also runs the scheduler container).
# Needs: docker, aws cli, an instance IAM role allowing ecr pull + ssm:GetParameter on /okghumo/*.
# Usage: ECR_IMAGE=<account>.dkr.ecr.ap-south-1.amazonaws.com/okghumo-chat:latest ./deploy.sh
set -euo pipefail

: "${ECR_IMAGE:?set ECR_IMAGE, e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com/okghumo-chat:latest}"
REGION="${AWS_REGION:-ap-south-1}"

param() { aws ssm get-parameter --region "$REGION" --name "/okghumo/$1" --with-decryption --query Parameter.Value --output text; }

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ECR_IMAGE%%/*}"
docker pull "$ECR_IMAGE"

docker rm -f chat 2>/dev/null || true
# bound to 127.0.0.1: only Caddy on this machine can reach the app
docker run -d --name chat --restart unless-stopped -p 127.0.0.1:8000:8000 \
  -e ANTHROPIC_API_KEY="$(param anthropic_key)" \
  -e JWT_SECRET="$(param jwt_secret)" \
  -e CREDENTIAL_KEY="$(param credential_key)" \
  -e DB_PATH=/data/app.db -v okghumo-data:/data \
  -e OKGHUMO_MCP_URL="https://mcp.okghumo.com/mcp" \
  -e CORS_ORIGINS="${CORS_ORIGINS:-}" \
  "$ECR_IMAGE" >/dev/null

for _ in $(seq 1 15); do
  curl -fs localhost:8000/health >/dev/null && { echo "deployed: healthy"; exit 0; }
  sleep 2
done
echo "deploy FAILED: /health did not respond" >&2
docker logs --tail 30 chat >&2
exit 1
