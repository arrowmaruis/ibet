"""Cache disque minimaliste (fichiers JSON) pour economiser le quota API."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ibet import chemins

CACHE_DIR = chemins.CACHE


def _key_to_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.json"


def get(key: str, ttl: int) -> Any | None:
    """Retourne la valeur en cache si elle existe et n'a pas expire, sinon None."""
    if ttl <= 0:
        return None
    path = _key_to_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - payload.get("stored_at", 0) > ttl:
        return None
    return payload.get("value")


def set(key: str, value: Any) -> None:  # noqa: A001 - API volontairement simple
    """Ecrit la valeur en cache. Un echec d'ecriture n'est jamais bloquant."""
    CACHE_DIR.mkdir(exist_ok=True)
    payload = {"key": key, "stored_at": time.time(), "value": value}
    try:
        _key_to_path(key).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def clear() -> int:
    """Vide le cache et retourne le nombre de fichiers supprimes."""
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        path.unlink()
        removed += 1
    return removed
