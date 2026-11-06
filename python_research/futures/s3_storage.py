"""Timeweb Cloud S3. Keys come from the environment or bybit.env, never from code."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

EMPTY = hashlib.sha256(b"").hexdigest()


def _env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    home = Path.home() / ".cursor"
    for path in (
        home / "bybit-top25-scan-state" / "bybit.env",
        home / "wallet-bybit-arb-state" / "bybit.env",
    ):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    return out


_warned_missing = False


def settings() -> dict[str, str] | None:
    global _warned_missing
    file_vals = _env_file()

    def pick(*names: str) -> str:
        for name in names:
            val = os.environ.get(name) or file_vals.get(name) or ""
            if val:
                return val
        return ""

    bucket = pick("S3_BUCKET", "AWS_BUCKET")
    access = pick("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
    secret = pick("S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
    if not bucket or not access or not secret:
        if not _warned_missing:
            _warned_missing = True
            missing = [n for n, v in (("S3_BUCKET", bucket), ("S3_ACCESS_KEY", access), ("S3_SECRET_KEY", secret)) if not v]
            print(f"[s3_storage] S3 disabled, empty env: {', '.join(missing)}", flush=True)
        return None
    endpoint = pick("S3_ENDPOINT") or "https://s3.twcstorage.ru"
    region = pick("S3_REGION") or "ru-1"
    return {
        "bucket": bucket,
        "access": access,
        "secret": secret,
        "endpoint": endpoint.rstrip("/"),
        "region": region,
        "prefix": (pick("S3_PREFIX") or "bybit-futures").strip("/"),
    }


def _sign(secret: str, date: str, region: str, string_to_sign: str) -> str:
    def mac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    key = mac(("AWS4" + secret).encode("utf-8"), date)
    key = mac(key, region)
    key = mac(key, "s3")
    key = mac(key, "aws4_request")
    return hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _request(method: str, url: str, body: bytes, content_type: str, cfg: dict[str, str]) -> bytes:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    host = cfg["endpoint"].split("://", 1)[-1]
    path = urllib.request.Request(url).selector or "/"
    if "?" in path:
        canonical_uri, query = path.split("?", 1)
    else:
        canonical_uri, query = path, ""
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers["content-type"] = content_type
    signed = sorted(headers)
    canonical = "\n".join([
        method,
        canonical_uri or "/",
        query,
        "".join(f"{name}:{headers[name]}\n" for name in signed),
        ";".join(signed),
        payload_hash,
    ])
    scope = f"{day}/{cfg['region']}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signature = _sign(cfg["secret"], day, cfg["region"], string_to_sign)
    auth = (
        f"AWS4-HMAC-SHA256 Credential={cfg['access']}/{scope}, "
        f"SignedHeaders={';'.join(signed)}, Signature={signature}"
    )
    req_headers = {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": auth,
    }
    if content_type:
        req_headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body if method != "GET" else None, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"s3 {method} {exc.code}: {detail}") from exc


def list_buckets(cfg: dict[str, str] | None = None) -> list[str]:
    cfg = cfg or settings()
    if cfg is None:
        return []
    raw = _request("GET", cfg["endpoint"] + "/", b"", "", cfg)
    root = ElementTree.fromstring(raw)
    names = []
    for node in root.iter():
        if node.tag.endswith("Name") and node.text:
            names.append(node.text)
    return names


def object_key(name: str, cfg: dict[str, str] | None = None) -> str:
    cfg = cfg or settings() or {}
    prefix = cfg.get("prefix") or "bybit-futures"
    return f"{prefix}/{name}"


class S3ReadError(RuntimeError):
    """Transient read failure (network / 5xx). Callers must NOT treat it as 'file absent'."""


def get_json_strict(name: str) -> dict | None:
    """Return the object, None only on an explicit 404, raise S3ReadError on any other failure."""
    cfg = settings()
    if cfg is None or not cfg["bucket"]:
        return None
    key = object_key(name, cfg)
    url = f"{cfg['endpoint']}/{cfg['bucket']}/{key}"
    try:
        raw = _request("GET", url, b"", "", cfg)
    except RuntimeError as exc:
        if " 404:" in str(exc):
            return None
        raise S3ReadError(str(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise S3ReadError(f"bad json in {name}: {exc}") from exc
    return data if isinstance(data, dict) else None


def get_json(name: str) -> dict | None:
    cfg = settings()
    if cfg is None or not cfg["bucket"]:
        return None
    key = object_key(name, cfg)
    url = f"{cfg['endpoint']}/{cfg['bucket']}/{key}"
    try:
        raw = _request("GET", url, b"", "", cfg)
    except RuntimeError as exc:
        if " 404:" in str(exc):
            return None
        print("  s3 read", name, exc, flush=True)
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def put_json(name: str, data: dict) -> bool:
    cfg = settings()
    if cfg is None or not cfg["bucket"]:
        return False
    key = object_key(name, cfg)
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    url = f"{cfg['endpoint']}/{cfg['bucket']}/{key}"
    try:
        _request("PUT", url, body, "application/json", cfg)
    except RuntimeError as exc:
        print("  s3 write", name, exc, flush=True)
        return False
    return True
