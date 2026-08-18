"""Credential stores.  Secrets are never represented in public state models."""

from __future__ import annotations

import os


class CredentialAccessError(RuntimeError):
    """Keychain/backend failure with a stable, secret-free recovery message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def translate_keyring_error(exc: BaseException) -> CredentialAccessError:
    """Map keyring/OS errors to a recovery code; never echo the backend text."""

    text = str(exc).casefold()
    if "user canceled" in text or "(-128" in text or "not authorized" in text:
        return CredentialAccessError(
            "denied",
            "钥匙串访问被拒绝；请解锁 Keychain 后重试",
        )
    if "locked" in text or "interaction not allowed" in text:
        return CredentialAccessError(
            "locked",
            "钥匙串已锁定或当前环境无法访问；请解锁 Keychain 后重试",
        )
    return CredentialAccessError(
        "unavailable",
        "凭据暂时不可用；请解锁 Keychain 或检查钥匙串权限后重试",
    )


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

        try:
            return keyring.get_password(self.service_name, ref)
        except Exception as exc:
            raise translate_keyring_error(exc) from None

    def set(self, ref: str, secret: str) -> None:
        import keyring

        try:
            keyring.set_password(self.service_name, ref, secret)
        except Exception as exc:
            raise translate_keyring_error(exc) from None

    def delete(self, ref: str) -> None:
        import keyring

        try:
            keyring.delete_password(self.service_name, ref)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise translate_keyring_error(exc) from None


def environment_credential(provider_id: str) -> str | None:
    key = f"MORROW_{provider_id.upper().replace('-', '_')}_API_KEY"
    return os.environ.get(key)
