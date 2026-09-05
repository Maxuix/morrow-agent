"""Task roles encoded by existing frozen node IDs, independent of Agent reuse.

Planner IDs are the role or role_<ordinal>. Keeping this existing convention
avoids changing immutable Revision schemas/hashes. Other user node IDs retain
the historical definition-origin fallback.
"""

PLANNED_ROLES = frozenset({"direct", "explorer", "planner", "coder", "reviewer", "synthesizer"})


def planned_node_id(role, ordinal, count):
    return f"{role}_{ordinal}" if count > 1 else role


def task_role(node_id, definition=None):
    role, separator, ordinal = node_id.rpartition("_")
    if node_id in PLANNED_ROLES:
        return node_id
    if (
        separator
        and role in PLANNED_ROLES
        and ordinal.isascii()
        and ordinal.isdecimal()
        and int(ordinal) > 0
    ):
        return role
    if definition is not None:
        origin = definition.derived_from_definition_id or definition.definition_id
        if origin.startswith("builtin_"):
            return origin.removeprefix("builtin_")
    return node_id
