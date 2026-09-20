"""Task roles encoded by current planner node IDs, independent of Agent reuse."""

PLANNED_ROLES = frozenset({"direct", "explorer", "planner", "coder", "reviewer", "synthesizer"})


def planned_node_id(role, ordinal, count):
    return f"{role}_{ordinal}" if count > 1 else role
