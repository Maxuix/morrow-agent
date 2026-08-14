"""Credential stores.  Secrets are never represented in public state models."""

from __future__ import annotations

import os


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, ref: str) -> str | None:
        return self._values.get(ref)

    def set(self, ref: str, secret: str) -> None:
        self._values[ref] = secret

    def delete(self, ref: str) -> None:
        self._values.pop(ref, None)


class KeyringCredentialStore:
    service_name = "morrow"

    def get(self, ref: str) -> str | None:
        import keyring

        return keyring.get_password(self.service_name, ref)

    def set(self, ref: str, secret: str) -> None:
        import keyring

        keyring.set_password(self.service_name, ref, secret)

    def delete(self, ref: str) -> None:
        import keyring

        try:
            keyring.delete_password(self.service_name, ref)
        except keyring.errors.PasswordDeleteError:
            pass


def environment_credential(provider_id: str) -> str | None:
    key = f"MORROW_{provider_id.upper().replace('-', '_')}_API_KEY"
    return os.environ.get(key)
