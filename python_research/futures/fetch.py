"""Public HTTP JSON. No API keys."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; bybit-top25-scan/1.0)"
_CTX = ssl.create_default_context()


def get_json(url: str, timeout: int = 20) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"HTTP {err.code} {url}: {body}") from err
