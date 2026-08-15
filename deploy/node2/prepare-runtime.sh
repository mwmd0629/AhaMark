#!/bin/sh
set -eu

deploy_root="${1:-/data/shr/ahamark-node2}"
expected_root="/data/shr/ahamark-node2"
release="5eda608"
run_id="node2-20260815-${release}"
runtime_env="${deploy_root}/runtime.env"
evidence_root="${deploy_root}/.preproduction-v8"
cert_dir="${evidence_root}/${run_id}/certs"

fail() {
    printf 'prepare-runtime: %s\n' "$1" >&2
    exit 1
}

[ "${HOME:-}" = "/data/shr" ] || fail "HOME must be /data/shr"
[ "$deploy_root" = "$expected_root" ] || fail "unexpected deploy root"
[ -d "$deploy_root" ] || fail "deploy root does not exist"
[ -f "$deploy_root/docker-compose.preproduction.yml" ] || fail "base compose missing"
[ -f "$deploy_root/docker-compose.node2.yml" ] || fail "node2 compose missing"
[ -f "$deploy_root/deploy/nginx/node2.conf" ] || fail "node2 nginx config missing"
[ ! -e "$runtime_env" ] || fail "runtime.env already exists"
[ ! -e "$cert_dir" ] || fail "certificate directory already exists"

if ss -H -ltn | awk '$4 ~ /:3300$/ { found=1 } END { exit !found }'; then
    fail "TCP port 3300 is already listening"
fi

[ -z "$(docker ps -aq --filter label=com.docker.compose.project=ahamark-node2)" ] || \
    fail "Compose project ahamark-node2 already has containers"
[ -z "$(docker volume ls -q --filter name='^ahamark-node2-')" ] || \
    fail "reserved ahamark-node2 volumes already exist"
[ -z "$(docker network ls -q --filter name='^ahamark-node2-default$')" ] || \
    fail "reserved ahamark-node2 network already exists"

umask 077
mkdir -p "$cert_dir"

openssl req \
    -x509 \
    -newkey rsa:3072 \
    -sha256 \
    -nodes \
    -days 365 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "$cert_dir/localhost.key" \
    -out "$cert_dir/localhost.crt" \
    >/dev/null 2>&1
chmod 600 "$cert_dir/localhost.key"
chmod 644 "$cert_dir/localhost.crt"

postgres_password="$(openssl rand -hex 32)"
session_secret="$(openssl rand -hex 48)"
minio_access_key="ahamark$(openssl rand -hex 8)"
minio_secret_key="$(openssl rand -hex 32)"
runtime_tmp="$(mktemp "${deploy_root}/.runtime.env.XXXXXX")"
trap 'rm -f "$runtime_tmp"' EXIT HUP INT TERM

{
    printf 'COMPOSE_PROJECT_NAME=ahamark-node2\n'
    printf 'POSTGRES_DB=ahamark\n'
    printf 'POSTGRES_USER=ahamark\n'
    printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
    printf 'PREPROD_HTTPS_PORT=3300\n'
    printf 'SESSION_HMAC_SECRET=%s\n' "$session_secret"
    printf 'MINIO_ACCESS_KEY=%s\n' "$minio_access_key"
    printf 'MINIO_SECRET_KEY=%s\n' "$minio_secret_key"
    printf 'MINIO_BUCKET=ahamark-files\n'
    printf 'PREPROD_RUN_ID=%s\n' "$run_id"
    printf 'PREPROD_EVIDENCE_ROOT=.preproduction-v8\n'
    printf 'POSTGRES_VOLUME=ahamark-node2-postgres-data\n'
    printf 'REDIS_VOLUME=ahamark-node2-redis-data\n'
    printf 'MINIO_VOLUME=ahamark-node2-minio-data\n'
    printf 'PREPROD_NETWORK=ahamark-node2-default\n'
    printf 'AHAMARK_API_IMAGE=ahamark/api:%s\n' "$release"
    printf 'AHAMARK_WEB_IMAGE=ahamark/web:%s\n' "$release"
    printf 'AHAMARK_POSTGRES_IMAGE=postgres:16-alpine\n'
    printf 'AHAMARK_REDIS_IMAGE=redis:7-alpine\n'
    printf 'AHAMARK_MINIO_IMAGE=minio/minio:RELEASE.2025-04-22T22-12-26Z\n'
    printf 'AHAMARK_NGINX_IMAGE=nginx:1.27-alpine\n'
    printf 'AHAMARK_ALPINE_IMAGE=alpine:3.22\n'
    printf 'AI_GRADING_PROVIDER=unavailable\n'
    printf 'ASSIGNMENT_GENERATION_ENABLED=true\n'
    printf 'ASSIGNMENT_GENERATION_PROVIDER=unavailable\n'
    printf 'ASSIGNMENT_GENERATION_ALLOW_EXTERNAL_PROVIDER_REQUESTS=false\n'
    printf 'ASSIGNMENT_GENERATION_ALLOW_TEACHER_START=true\n'
    printf 'ASSIGNMENT_GENERATION_SUGGESTION_ONLY=true\n'
    printf 'ASSIGNMENT_GENERATION_REAL_PROVIDER_QUALITY_PASSED=false\n'
} >"$runtime_tmp"

chmod 600 "$runtime_tmp"
mv "$runtime_tmp" "$runtime_env"
trap - EXIT HUP INT TERM

docker compose \
    --env-file "$runtime_env" \
    -f "$deploy_root/docker-compose.preproduction.yml" \
    -f "$deploy_root/docker-compose.node2.yml" \
    config --quiet

printf 'prepare-runtime: ready\n'
printf 'deploy_root=%s\n' "$deploy_root"
printf 'runtime_env_mode=%s\n' "$(stat -c '%a' "$runtime_env")"
printf 'certificate_key_mode=%s\n' "$(stat -c '%a' "$cert_dir/localhost.key")"
printf 'certificate_sha256=%s\n' "$(sha256sum "$cert_dir/localhost.crt" | awk '{print $1}')"
printf 'secrets_printed=false\n'
