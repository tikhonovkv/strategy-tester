"""Bybit v5 REST: public market + signed trading. Keys only from env files."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fetch import UA

BASE = "https://api.bybit.com"
RECV = "5000"
_CTX = ssl.create_default_context()


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class BybitKeys:
    api_key: str
    api_secret: str


def load_keys() -> BybitKeys:
    home = Path.home() / ".cursor"
    file_vals: dict[str, str] = {}
    for path in (
        home / "bybit-top25-scan-state" / "bybit.env",
        home / "wallet-bybit-arb-state" / "bybit.env",
    ):
        file_vals = {**file_vals, **_parse_env(path)}
        if file_vals.get("BYBIT_API_KEY") and file_vals.get("BYBIT_API_SECRET"):
            break
    key = os.environ.get("BYBIT_API_KEY") or file_vals.get("BYBIT_API_KEY") or ""
    secret = os.environ.get("BYBIT_API_SECRET") or file_vals.get("BYBIT_API_SECRET") or ""
    if not key or not secret:
        raise SystemExit("нет BYBIT_API_KEY / BYBIT_API_SECRET в bybit.env")
    return BybitKeys(key, secret)


class BybitClient:
    def __init__(self, keys: BybitKeys | None = None) -> None:
        self._keys = keys
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_CTX),
        )

    def public_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        query = urllib.parse.urlencode(params or {})
        url = f"{BASE}{path}"
        if query:
            url = f"{url}?{query}"
        return self._send("GET", url, body=None, headers={"User-Agent": UA, "Accept": "application/json"})

    def private_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        keys = self._require_keys()
        query = urllib.parse.urlencode(params or {})
        timestamp = str(int(time.time() * 1000))
        sign = self._sign(keys, timestamp, query)
        url = f"{BASE}{path}"
        if query:
            url = f"{url}?{query}"
        return self._send("GET", url, body=None, headers=self._auth_headers(keys, timestamp, sign))

    def private_post(self, path: str, payload: dict) -> dict:
        keys = self._require_keys()
        body = json.dumps(payload, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        sign = self._sign(keys, timestamp, body)
        url = f"{BASE}{path}"
        return self._send("POST", url, body=body.encode("utf-8"), headers=self._auth_headers(keys, timestamp, sign))

    def _require_keys(self) -> BybitKeys:
        if self._keys is None:
            self._keys = load_keys()
        return self._keys

    def _sign(self, keys: BybitKeys, timestamp: str, payload: str) -> str:
        raw = f"{timestamp}{keys.api_key}{RECV}{payload}"
        return hmac.new(keys.api_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def _auth_headers(self, keys: BybitKeys, timestamp: str, sign: str) -> dict[str, str]:
        return {
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": keys.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": sign,
            "X-BAPI-RECV-WINDOW": RECV,
            "X-BAPI-SIGN-TYPE": "2",
        }

    def _send(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> dict:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Bybit HTTP {err.code}: {raw}") from err
        if int(data.get("retCode") or 0) != 0:
            raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
        return data
