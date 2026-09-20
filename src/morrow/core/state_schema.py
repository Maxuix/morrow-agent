"""Independent state-document schema constants.

State documents use named constants so one domain cannot silently advance
another domain's schema. Event envelope versions remain owned by the event model.
"""

GLOBAL_CONFIG_SCHEMA_VERSION = 2
WORKSPACE_PREFERENCE_SCHEMA_VERSION = 3
WORKSPACE_PROFILE_SCHEMA_VERSION = 2
WORKSPACE_INDEX_SCHEMA_VERSION = 2


__all__ = [
    "GLOBAL_CONFIG_SCHEMA_VERSION",
    "WORKSPACE_INDEX_SCHEMA_VERSION",
    "WORKSPACE_PREFERENCE_SCHEMA_VERSION",
    "WORKSPACE_PROFILE_SCHEMA_VERSION",
]
