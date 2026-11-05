# -*- coding: utf-8 -*-
"""Multi-account configuration and secret cipher backed by Timeweb S3.

Stores `accounts_config.json` in S3 (with local fallback cache) so accounts,
API keys, individual PIN codes, leverage, risk, strategy, and exchange settings
can be added, edited, enabled, or disabled from the web UI without redeploying.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import s3_storage
from causal import LAB

OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
CONFIG_NAME = "accounts_config.json"
BACKUP_NAME = "accounts_config.bak.json"
DEFAULT_MASTER_PIN = "540502"
ENC_PREFIX = "enc1:"


def _cipher_secret() -> str:
    cfg = s3_storage.settings() or {}
    sec = (
        os.environ.get("ACCOUNTS_CIPHER_KEY")
        or cfg.get("secret")
        or os.environ.get("S3_SECRET_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or "bybit-default-cipher-key-540502"
    )
    return str(sec)


def _keystream(key_bytes: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key_bytes,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    if plain.startswith(ENC_PREFIX):
        return plain
    raw = plain.encode("utf-8")
    master = hashlib.sha256(("accounts-cipher:" + _cipher_secret()).encode("utf-8")).digest()
    nonce = secrets.token_bytes(12)
    stream = _keystream(master, nonce, len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    mac = hmac.new(master, nonce + cipher, hashlib.sha256).digest()[:16]
    blob = base64.urlsafe_b64encode(nonce + mac + cipher).decode("ascii")
    return ENC_PREFIX + blob


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return value
    b64 = value[len(ENC_PREFIX):]
    padding = "=" * ((4 - len(b64) % 4) % 4)
    try:
        packed = base64.urlsafe_b64decode(b64 + padding)
    except Exception:
        return ""
    if len(packed) < 28:
        return ""
    nonce = packed[:12]
    mac = packed[12:28]
    cipher = packed[28:]
    master = hashlib.sha256(("accounts-cipher:" + _cipher_secret()).encode("utf-8")).digest()
    expected = hmac.new(master, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        return ""
    stream = _keystream(master, nonce, len(cipher))
    plain = bytes(a ^ b for a, b in zip(cipher, stream))
    try:
        return plain.decode("utf-8")
    except Exception:
        return ""


def _default_env_keys() -> tuple[str, str]:
    home = Path.home() / ".cursor"
    file_vals: dict[str, str] = {}
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
            k, _, v = line.partition("=")
            file_vals[k.strip()] = v.strip().strip('"').strip("'")
        if file_vals.get("BYBIT_API_KEY") and file_vals.get("BYBIT_API_SECRET"):
            break
    key = os.environ.get("BYBIT_API_KEY") or file_vals.get("BYBIT_API_KEY") or ""
    sec = os.environ.get("BYBIT_API_SECRET") or file_vals.get("BYBIT_API_SECRET") or ""
    return key, sec


def _load_default_params() -> dict:
    for d in (OUT, LAB):
        p = d / "live_params.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def default_config() -> dict:
    params = _load_default_params()
    key, sec = _default_env_keys()
    return {
        "version": 1,
        "master_pin": DEFAULT_MASTER_PIN,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "accounts": [
            {
                "id": "main",
                "name": "Основной Bybit (7x)",
                "enabled": True,
                "pause_new_trades": False,
                "pin": DEFAULT_MASTER_PIN,
                "exchange": "bybit",
                "strategy": "schwartz_7x_sleeve_3x",
                "leverage": float(params.get("leverage", 7.0)),
                "sleeve_leverage": float(params.get("sleeve_leverage", 3.0)),
                "risk_pct": float(params.get("risk_pct", 0.075)),
                "max_margin_frac": float(params.get("max_margin_frac", 0.60)),
                "max_conc": int(params.get("max_conc", 2)),
                "enable_sleeve": bool(params.get("enable_sleeve", True)),
                "api_key": encrypt_secret(key) if key else "",
                "api_secret": encrypt_secret(sec) if sec else "",
            }
        ],
    }


def _normalize_account(raw: dict, is_first: bool = False) -> dict:
    acc_id = str(raw.get("id") or ("main" if is_first else "bot2")).strip().lower()
    acc_id = "".join(ch for ch in acc_id if ch.isalnum() or ch in ("_", "-")) or "main"
    api_key = decrypt_secret(str(raw.get("api_key") or ""))
    api_secret = decrypt_secret(str(raw.get("api_secret") or ""))
    if acc_id == "main" and (not api_key or not api_secret):
        def_k, def_s = _default_env_keys()
        api_key = api_key or def_k
        api_secret = api_secret or def_s
    return {
        "id": acc_id,
        "name": str(raw.get("name") or f"Бот {acc_id}"),
        "enabled": bool(raw.get("enabled", True)),
        "pause_new_trades": bool(raw.get("pause_new_trades", False)),
        "pin": str(raw.get("pin") or DEFAULT_MASTER_PIN).strip(),
        "exchange": str(raw.get("exchange") or "bybit").strip().lower(),
        "strategy": str(raw.get("strategy") or "schwartz_7x_sleeve_3x").strip(),
        "leverage": float(raw.get("leverage") or 7.0),
        "sleeve_leverage": float(raw.get("sleeve_leverage") or 3.0),
        "risk_pct": float(raw.get("risk_pct") or 0.075),
        "max_margin_frac": float(raw.get("max_margin_frac") or 0.60),
        "max_conc": int(raw.get("max_conc") or 2),
        "enable_sleeve": bool(raw.get("enable_sleeve", True)),
        "api_key": api_key,
        "api_secret": api_secret,
    }


def load_accounts_config(seed_if_missing: bool = True) -> dict:
    data = None
    try:
        data = s3_storage.get_json(CONFIG_NAME)
    except Exception:
        data = None
    if not isinstance(data, dict) or not data.get("accounts"):
        for d in (OUT, LAB):
            p = d / CONFIG_NAME
            if p.exists():
                try:
                    cand = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(cand, dict) and cand.get("accounts"):
                        data = cand
                        break
                except Exception:
                    pass
    if not isinstance(data, dict) or not data.get("accounts"):
        data = default_config()
        if seed_if_missing:
            save_accounts_config(data)

    raw_accounts = data.get("accounts") or []
    accounts = []
    seen = set()
    for idx, r in enumerate(raw_accounts):
        if not isinstance(r, dict):
            continue
        norm = _normalize_account(r, is_first=(idx == 0))
        if norm["id"] in seen:
            continue
        seen.add(norm["id"])
        accounts.append(norm)
    if not accounts:
        accounts = [_normalize_account({}, is_first=True)]
    return {
        "version": int(data.get("version") or 1),
        "master_pin": str(data.get("master_pin") or DEFAULT_MASTER_PIN),
        "updated_at": str(data.get("updated_at") or ""),
        "accounts": accounts,
    }


def save_accounts_config(cfg: dict) -> bool:
    stored_accounts = []
    for idx, acc in enumerate(cfg.get("accounts") or []):
        norm = _normalize_account(acc, is_first=(idx == 0))
        stored_accounts.append({
            **norm,
            "api_key": encrypt_secret(norm["api_key"]) if norm["api_key"] else "",
            "api_secret": encrypt_secret(norm["api_secret"]) if norm["api_secret"] else "",
        })
    payload = {
        "version": int(cfg.get("version") or 1),
        "master_pin": str(cfg.get("master_pin") or DEFAULT_MASTER_PIN),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "accounts": stored_accounts,
    }
    for d in {LAB, OUT}:
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{CONFIG_NAME}.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, d / CONFIG_NAME)
        except Exception:
            pass
    try:
        prev = s3_storage.get_json(CONFIG_NAME)
        if isinstance(prev, dict) and prev.get("accounts"):
            s3_storage.put_json(BACKUP_NAME, prev)
    except Exception:
        pass
    try:
        return s3_storage.put_json(CONFIG_NAME, payload)
    except Exception:
        return False


def get_account(account_id: str | None = None) -> dict:
    cfg = load_accounts_config()
    target = (account_id or "main").strip().lower()
    for acc in cfg["accounts"]:
        if acc["id"] == target:
            return acc
    return cfg["accounts"][0]


def enabled_accounts() -> list[dict]:
    cfg = load_accounts_config()
    return [a for a in cfg["accounts"] if a.get("enabled", True)]


def parse_account_arg(argv: list[str] | None = None) -> str:
    args = argv if argv is not None else sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--account" and i + 1 < len(args):
            return str(args[i + 1]).strip().lower()
        if arg.startswith("--account="):
            return arg.split("=", 1)[1].strip().lower()
    return os.environ.get("BOT_ACCOUNT_ID", "main").strip().lower() or "main"


def account_filename(base_stem: str, account_id: str) -> str:
    acc = (account_id or "main").strip().lower()
    return f"{base_stem}.{acc}.json"


def read_account_json(base_stem: str, account_id: str) -> dict:
    acc = (account_id or "main").strip().lower()
    names = [account_filename(base_stem, acc)]
    if acc == "main":
        names.append(f"{base_stem}.json")
    # Prefer S3 first so stale git-tracked files never overwrite live container state
    for name in names:
        try:
            remote = s3_storage.get_json(name)
            if isinstance(remote, dict) and remote:
                return remote
        except Exception:
            pass
    for name in names:
        for d in (OUT, LAB):
            p = d / name
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data:
                        return data
                except Exception:
                    pass
    return {}


def write_account_json(base_stem: str, account_id: str, payload: dict, persist: bool = True) -> None:
    acc = (account_id or "main").strip().lower()
    names = [account_filename(base_stem, acc)]
    if acc == "main":
        names.append(f"{base_stem}.json")
    text = json.dumps(payload, ensure_ascii=False)
    for name in names:
        for d in {LAB, OUT}:
            try:
                tmp = d / f"{name}.tmp"
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, d / name)
            except Exception as exc:
                print(f"  local write {name}: {exc}", flush=True)
        if persist:
            try:
                s3_storage.put_json(name, payload)
            except Exception as exc:
                print(f"  s3 write {name}: {exc}", flush=True)
