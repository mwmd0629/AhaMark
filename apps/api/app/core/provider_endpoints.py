from __future__ import annotations

import ipaddress
import urllib.parse


class ProviderEndpointError(ValueError):
    pass


def safe_provider_base_url(
    value: str,
    *,
    allow_external_https: bool,
    allow_local_http: bool,
    allowed_local_hosts: list[str],
) -> str:
    """Validate a provider endpoint without widening the production SSRF boundary."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderEndpointError("provider_endpoint_not_allowed")
    host = parsed.hostname.rstrip(".").lower()
    local_hosts = {item.rstrip(".").lower() for item in allowed_local_hosts}
    if allow_local_http and parsed.scheme == "http" and host in local_hosts:
        if host in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
            raise ProviderEndpointError("provider_endpoint_not_allowed")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ProviderEndpointError("provider_endpoint_not_allowed")
        return value.rstrip("/")
    if not allow_external_https or parsed.scheme != "https":
        raise ProviderEndpointError("provider_endpoint_not_allowed")
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        raise ProviderEndpointError("provider_endpoint_not_allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ProviderEndpointError("provider_endpoint_not_allowed")
    if host.endswith((".local", ".internal", ".localhost")):
        raise ProviderEndpointError("provider_endpoint_not_allowed")
    return value.rstrip("/")
