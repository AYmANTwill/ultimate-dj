"""
Secrets storage — Windows Credential Manager via the `keyring` package.

Why this exists:
    The previous storage put the Spotify Client ID + Secret in plaintext
    inside ``data/config.json``. That's reasonable for a single-user dev
    setup, but it leaks the creds to anyone with disk access (or a
    badly-permissioned backup). Audit finding S-1 flagged this as HIGH.

What this does:
    Wraps keyring with a tiny API that the rest of the app uses. On
    Windows, keyring's WinVaultKeyring backend stores secrets in the
    Windows Credential Manager (encrypted by DPAPI per user).

Migration:
    On first launch after the upgrade, ``ensure_migrated()`` reads the
    legacy config.json values, pushes them into keyring, then BLANKS the
    config.json fields. The user never has to do anything manually.
"""
from __future__ import annotations

from typing import Optional

# Single service prefix in Windows Credential Manager so all entries
# show up grouped under "Ultimate DJ — *". Use "ultimatedj-<name>" as
# the service name.
_SVC_PREFIX = "ultimatedj"
# Per-user identifier inside that service. We don't have multiple users,
# so a constant works.
_ACCOUNT = "default"


def _keyring():
    """Lazy import — splash auto-installer hasn't always run by the time
    config.py is imported. Returns None if keyring is unavailable so we
    can fall back to plaintext (with a warning logged)."""
    try:
        import keyring
        return keyring
    except Exception:
        return None


def get_secret(name: str) -> Optional[str]:
    """Return the secret stored under `name`, or None if not set / no
    keyring backend available."""
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(f"{_SVC_PREFIX}-{name}", _ACCOUNT)
    except Exception:
        return None


def set_secret(name: str, value: str) -> bool:
    """Persist `value` under `name`. Returns True on success.

    Empty / None value DELETES the secret (so blanking a credential in
    Settings actually clears it instead of leaving a stale value)."""
    kr = _keyring()
    if kr is None:
        return False
    svc = f"{_SVC_PREFIX}-{name}"
    try:
        if not value:
            try:
                kr.delete_password(svc, _ACCOUNT)
            except Exception:
                pass
            return True
        kr.set_password(svc, _ACCOUNT, value)
        return True
    except Exception:
        return False


# ── One-shot migration from legacy config.json ────────────────────

def ensure_migrated() -> None:
    """Move plaintext Spotify creds out of config.json into the keyring
    on first launch after upgrade. Idempotent — safe to call every boot.

    After migration the config.json fields are blanked; readers should
    use `get_spotify_credentials()` below, not the raw config.
    """
    from app.config import load_config, save_config
    cfg = load_config()
    cid = (cfg.get("spotify_client_id") or "").strip()
    secret = (cfg.get("spotify_client_secret") or "").strip()
    moved = False
    if cid:
        if set_secret("spotify_client_id", cid):
            cfg["spotify_client_id"] = ""
            moved = True
    if secret:
        if set_secret("spotify_client_secret", secret):
            cfg["spotify_client_secret"] = ""
            moved = True
    if moved:
        save_config(cfg)


def get_spotify_credentials() -> tuple[str, str]:
    """(client_id, client_secret) — keyring first, config.json fallback.
    Empty strings if neither is set."""
    cid = get_secret("spotify_client_id") or ""
    sec = get_secret("spotify_client_secret") or ""
    if not (cid and sec):
        # Legacy fallback (pre-migration or keyring unavailable)
        try:
            from app.config import load_config
            cfg = load_config()
            cid = cid or (cfg.get("spotify_client_id") or "")
            sec = sec or (cfg.get("spotify_client_secret") or "")
        except Exception:
            pass
    return cid, sec


def set_spotify_credentials(client_id: str, client_secret: str) -> None:
    """Store both creds in the keyring AND clear the config.json copies
    so they don't leak."""
    set_secret("spotify_client_id", client_id)
    set_secret("spotify_client_secret", client_secret)
    try:
        from app.config import load_config, save_config
        cfg = load_config()
        cfg["spotify_client_id"] = ""
        cfg["spotify_client_secret"] = ""
        save_config(cfg)
    except Exception:
        pass


# ── 1001tracklists account (Win Credential Manager) ──────────────

def get_1001tracklists_credentials() -> tuple[str, str]:
    """(email, password) for the user's 1001tracklists account. Empty
    strings if unset. Used by engine.tracklists for the authenticated
    Playwright login flow that bypasses the guest IP rate-limit."""
    return (get_secret("1001tracklists_email") or "",
            get_secret("1001tracklists_password") or "")


def set_1001tracklists_credentials(email: str, password: str) -> None:
    """Store the 1001tracklists login in Windows Credential Manager."""
    set_secret("1001tracklists_email", email)
    set_secret("1001tracklists_password", password)
