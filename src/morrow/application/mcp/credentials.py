"""Explicit stdio credential bindings; values live only in CredentialStore and child env."""

import re

from morrow.adapters.mcp.stdio_client import McpAdapterError
from morrow.core.domain import sha256_digest

_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RESERVED = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "ENV",
        "BASH_ENV",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "RUBYOPT",
        "PERL5OPT",
        "JAVA_TOOL_OPTIONS",
    }
)


def validate_environment_name(name):
    if not _ENV.fullmatch(name) or name in _RESERVED or name.startswith(("LD_", "DYLD_")):
        raise ValueError("MCP credential environment name is invalid")
    return name


def _prefix(definition):
    owner = definition.scope + ":" + (definition.scope_id or "") + ":" + definition.server_id
    return "mcp:" + sha256_digest(owner)[:24] + ":"


def credential_reference(definition, name):
    return _prefix(definition) + validate_environment_name(name)


def credential_bindings(definition):
    result = {}
    for reference in definition.credential_refs:
        prefix = _prefix(definition)
        # Existing CLI refs that are environment names keep a direct, explicit mapping.
        name = reference[len(prefix) :] if reference.startswith(prefix) else reference
        try:
            validate_environment_name(name)
        except ValueError:
            raise McpAdapterError("credential_binding_invalid", "credentials") from None
        if name in result:
            raise McpAdapterError("credential_binding_duplicate", "credentials")
        result[name] = reference
    return result


def resolve_environment(definition, store):
    values = {}
    for name, reference in credential_bindings(definition).items():
        try:
            value = store.get(reference)
        except Exception:
            raise McpAdapterError("credential_unavailable", "credentials") from None
        if not value:
            raise McpAdapterError("credential_missing", "credentials")
        values[name] = value
    return values
