from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_node2_publishes_only_nginx_on_the_high_port() -> None:
    compose = (ROOT / "docker-compose.node2.yml").read_text(encoding="utf-8")
    prepare = (ROOT / "deploy" / "node2" / "prepare-runtime.sh").read_text(encoding="utf-8")

    assert "ports: !override" in compose
    assert '"0.0.0.0:${PREPROD_HTTPS_PORT:?set PREPROD_HTTPS_PORT}:8443"' in compose
    assert compose.count("ports:") == 1
    assert 'https_port="13300"' in prepare
    assert 'public_host="222.195.89.236"' in prepare
    assert 'internal_host="192.168.2.5"' in prepare


def test_node2_public_origin_is_consistent_and_logs_exclude_query_strings() -> None:
    compose = (ROOT / "docker-compose.node2.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy" / "nginx" / "node2.conf").read_text(encoding="utf-8")

    for setting in (
        "CORS_ORIGINS",
        "TRUSTED_HOSTS",
        "CSRF_TRUSTED_ORIGINS",
        "MINIO_PUBLIC_ENDPOINT",
    ):
        assert setting in compose
    assert "NODE2_PUBLIC_HOST" in compose
    assert "NODE2_INTERNAL_HOST" in compose
    assert "server_name localhost 127.0.0.1 192.168.2.5 222.195.89.236;" in nginx
    assert "IP:${internal_host},IP:${public_host}" in (
        ROOT / "deploy" / "node2" / "prepare-runtime.sh"
    ).read_text(encoding="utf-8")
    assert '"$request_method $uri $server_protocol"' in nginx
    assert '"$request"' not in nginx
