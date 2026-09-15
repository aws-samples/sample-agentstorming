# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Client-side key + token vault with pluggable storage backends.

Selection via ``AGENTSTORMING_VAULT_BACKEND`` env var:

- ``file`` (default): JSON at ``$AGENTSTORMING_KEY_DIR/vault.json``,
  0600 perms. Relies on disk encryption + filesystem access controls.
- ``secretsmanager``: AWS Secrets Manager (one secret per vault, JSON
  payload, task-role fetched). Used on Fargate where no persistent
  disk is available. Secret id: ``AGENTSTORMING_VAULT_SECRET_ID``.
- ``keychain``: platform keychain via the ``keyring`` package
  (macOS Keychain / libsecret / DPAPI). Requires ``pip install keyring``.
  Service+account set by ``AGENTSTORMING_VAULT_KEYCHAIN_SERVICE`` /
  ``AGENTSTORMING_VAULT_KEYCHAIN_ACCOUNT``.
- ``tpm`` / ``pkcs11``: stubbed. Concrete implementations require
  platform-specific libraries and live hardware.

Public API is unchanged: ``Vault(path=None)`` + ``ensure_keypair`` /
``store_*`` / ``get_*``. The constructor reads the env var and picks
the right backend automatically.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .signing import b64url, b64url_decode

log = logging.getLogger(__name__)


@dataclass
class VaultData:
    private_key_b64: str | None = None
    public_key_b64: str | None = None
    access_token: str | None = None
    access_expires_at: str | None = None
    refresh_token: str | None = None
    refresh_expires_at: str | None = None
    pid: str | None = None
    peer_pubkeys: dict[str, str] | None = None  # pid -> base64url
    server_pubkey: str | None = None
    since_cursor: int = -1
    extra: dict[str, Any] | None = None


# ------------------------------------------------------------------
# Backend protocol
# ------------------------------------------------------------------


class VaultBackend(Protocol):
    def load(self) -> VaultData: ...
    def save(self, data: VaultData) -> None: ...


# ------------------------------------------------------------------
# File backend (default)
# ------------------------------------------------------------------


class FileBackend:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> VaultData:
        if not self.path.exists():
            return VaultData()
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            return VaultData()
        return VaultData(**raw)

    def save(self, data: VaultData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except PermissionError:
            pass
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(data), indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


# ------------------------------------------------------------------
# Secrets Manager backend
# ------------------------------------------------------------------


class SecretsManagerBackend:
    """Store the vault JSON in a single AWS Secrets Manager secret.

    Suitable for ECS Fargate tasks that have no persistent disk but do
    have a task role permitting ``secretsmanager:Get/Put/CreateSecret``
    on a specific secret ARN.
    """

    def __init__(self, secret_id: str, region: str | None = None) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "SecretsManagerBackend requires boto3; "
                "install agentstorming-client[secretsmanager]"
            ) from e
        self.secret_id = secret_id
        self._client = boto3.client("secretsmanager", region_name=region)

    def load(self) -> VaultData:
        try:
            resp = self._client.get_secret_value(SecretId=self.secret_id)
        except Exception as e:  # ResourceNotFoundException is the common case
            name = type(e).__name__
            if name == "ResourceNotFoundException":
                return VaultData()
            raise
        body = resp.get("SecretString") or ""
        if not body:
            return VaultData()
        try:
            return VaultData(**json.loads(body))
        except Exception:
            return VaultData()

    def save(self, data: VaultData) -> None:
        body = json.dumps(asdict(data))
        try:
            self._client.put_secret_value(SecretId=self.secret_id, SecretString=body)
        except Exception as e:
            if type(e).__name__ == "ResourceNotFoundException":
                self._client.create_secret(Name=self.secret_id, SecretString=body)
                return
            raise


# ------------------------------------------------------------------
# Keychain backend (optional dep on `keyring`)
# ------------------------------------------------------------------


class KeychainBackend:
    def __init__(self, service: str, account: str) -> None:
        try:
            import keyring  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "KeychainBackend requires keyring; "
                "install agentstorming-client[keychain]"
            ) from e
        self._keyring = keyring
        self.service = service
        self.account = account

    def load(self) -> VaultData:
        raw = self._keyring.get_password(self.service, self.account)
        if not raw:
            return VaultData()
        try:
            return VaultData(**json.loads(raw))
        except Exception:
            return VaultData()

    def save(self, data: VaultData) -> None:
        self._keyring.set_password(self.service, self.account, json.dumps(asdict(data)))


# ------------------------------------------------------------------
# Stubs
# ------------------------------------------------------------------


class TPMBackend:  # pragma: no cover — requires TPM 2.0 hardware
    def __init__(self, handle: str) -> None:
        raise NotImplementedError(
            "TPMBackend is a design stub; implement against your TPM 2.0 PAPI "
            "(e.g. tpm2-pytss) before using."
        )


class PKCS11Backend:  # pragma: no cover — requires PKCS#11 + HSM
    def __init__(self, library: str, slot: int) -> None:
        raise NotImplementedError(
            "PKCS11Backend is a design stub; implement against your HSM's "
            "PKCS#11 library (e.g. python-pkcs11) before using."
        )


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


def _choose_backend(path: Path | None) -> VaultBackend:
    choice = os.environ.get("AGENTSTORMING_VAULT_BACKEND", "file").lower()
    if choice == "file":
        if path is None:
            base = Path(
                os.environ.get(
                    "AGENTSTORMING_KEY_DIR", str(Path.home() / ".config" / "agentstorming")
                )
            )
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = base / "vault.json"
        return FileBackend(path)
    if choice == "secretsmanager":
        secret_id = os.environ.get("AGENTSTORMING_VAULT_SECRET_ID")
        region = os.environ.get("AGENTSTORMING_VAULT_SECRET_REGION") or os.environ.get("AWS_REGION")
        if not secret_id:
            raise RuntimeError(
                "AGENTSTORMING_VAULT_BACKEND=secretsmanager requires AGENTSTORMING_VAULT_SECRET_ID"
            )
        return SecretsManagerBackend(secret_id, region=region)
    if choice == "keychain":
        service = os.environ.get("AGENTSTORMING_VAULT_KEYCHAIN_SERVICE", "agentstorming")
        account = os.environ.get("AGENTSTORMING_VAULT_KEYCHAIN_ACCOUNT") or str(path or "default")
        return KeychainBackend(service, account)
    if choice == "tpm":
        handle = os.environ.get("AGENTSTORMING_VAULT_TPM_HANDLE", "")
        return TPMBackend(handle)
    if choice == "pkcs11":
        lib = os.environ.get("AGENTSTORMING_VAULT_PKCS11_LIB", "")
        slot = int(os.environ.get("AGENTSTORMING_VAULT_PKCS11_SLOT", "0"))
        return PKCS11Backend(lib, slot)
    raise RuntimeError(f"Unknown vault backend: {choice}")


class Vault:
    """Public vault API. Dispatches to the configured backend."""

    def __init__(self, path: Path | None = None, *, backend: VaultBackend | None = None) -> None:
        self.backend = backend or _choose_backend(path)
        self.path = path if isinstance(self.backend, FileBackend) else None
        self.data = self.backend.load()

    def save(self) -> None:
        self.backend.save(self.data)

    # ---- key management

    def ensure_keypair(self) -> tuple[bytes, bytes]:
        if self.data.private_key_b64 and self.data.public_key_b64:
            return (
                b64url_decode(self.data.private_key_b64),
                b64url_decode(self.data.public_key_b64),
            )
        from .signing import generate_keypair
        priv, pub = generate_keypair()
        self.data.private_key_b64 = b64url(priv)
        self.data.public_key_b64 = b64url(pub)
        self.save()
        return priv, pub

    def rotate_keypair(self, new_priv: bytes, new_pub: bytes) -> None:
        """Persist a freshly rotated keypair. Caller must broadcast the
        org.agentstorming.key_rotation event FIRST (signed by the OLD
        key + co-signed by the new). This method replaces the stored
        keys so subsequent events are signed with the new key."""
        self.data.private_key_b64 = b64url(new_priv)
        self.data.public_key_b64 = b64url(new_pub)
        self.save()

    def store_tokens(
        self, access: str, access_expires: str, refresh: str, refresh_expires: str
    ) -> None:
        self.data.access_token = access
        self.data.access_expires_at = access_expires
        self.data.refresh_token = refresh
        self.data.refresh_expires_at = refresh_expires
        self.save()

    def store_pid(self, pid: str) -> None:
        self.data.pid = pid
        self.save()

    def store_peer_pubkey(self, pid: str, pubkey_b64: str) -> None:
        if self.data.peer_pubkeys is None:
            self.data.peer_pubkeys = {}
        self.data.peer_pubkeys[pid] = pubkey_b64
        self.save()

    def get_peer_pubkey(self, pid: str) -> bytes | None:
        if not self.data.peer_pubkeys:
            return None
        b = self.data.peer_pubkeys.get(pid)
        return b64url_decode(b) if b else None

    def store_server_pubkey(self, pubkey_b64: str) -> None:
        self.data.server_pubkey = pubkey_b64
        self.save()

    def get_server_pubkey(self) -> bytes | None:
        return b64url_decode(self.data.server_pubkey) if self.data.server_pubkey else None

    def store_since(self, seq: int) -> None:
        self.data.since_cursor = seq
        self.save()
