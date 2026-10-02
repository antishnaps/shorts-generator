"""Proxy discovery helpers used by YouTube download methods."""

import os
import socket
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

from .utils import _dummy_log


_VPN_CLIENTS = (
    ("Shadowsocks", 1080, "socks5"),
    ("V2Ray", 10808, "socks5"),
    ("Clash", 7890, "http"),
    ("Tor", 9150, "socks5"),
)

_vpn_cache = {"proxy": None, "timestamp": 0.0}
_vpn_cache_ttl = 300


def reset_proxy_cache() -> None:
    """Clear cached proxy detection results."""
    global _vpn_cache
    _vpn_cache = {"proxy": None, "timestamp": 0.0}


def detect_vpn_proxy(log_callback: Optional[Callable] = None) -> Optional[str]:
    """Return the first known local VPN proxy with an open port."""
    log = log_callback or _dummy_log

    for name, port, protocol in _VPN_CLIENTS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    proxy_url = f"{protocol}://127.0.0.1:{port}"
                    log(f"Automatically detected VPN: {name} ({proxy_url})")
                    return proxy_url
        except OSError:
            continue

    return None


def get_auto_proxy(log_callback: Optional[Callable] = None) -> Optional[str]:
    """Resolve a configured or local VPN proxy and cache the result."""
    global _vpn_cache

    log = log_callback or _dummy_log
    current_time = time.time()
    if current_time - _vpn_cache["timestamp"] < _vpn_cache_ttl:
        return _vpn_cache["proxy"]

    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy_url:
        log(f"Using proxy from environment: {describe_proxy(proxy_url)}")
    else:
        proxy_url = detect_vpn_proxy(log)

    _vpn_cache = {"proxy": proxy_url, "timestamp": current_time}
    return proxy_url


def describe_proxy(proxy_url: Optional[str]) -> str:
    """Describe a proxy without exposing embedded usernames or passwords."""

    value = str(proxy_url or "").strip()
    if not value:
        return "not configured"
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return "configured"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except (TypeError, ValueError):
        return "configured"


# Compatibility aliases for callers that imported the old private helpers.
_detect_vpn_clients = detect_vpn_proxy
_get_auto_proxy = get_auto_proxy


__all__ = [
    "detect_vpn_proxy",
    "describe_proxy",
    "get_auto_proxy",
    "reset_proxy_cache",
    "_detect_vpn_clients",
    "_get_auto_proxy",
]
