"""Read-only source fixtures. Publication is explicit and never a startup action."""

from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement


def builtin_definitions(exact_model):
    return (
        AgentDefinitionSource(
            definition_id="builtin_direct",
            name="Direct",
            role_prompt="Complete the task and verify the result.",
            model_selection=exact_model,
            access_mode_ceiling="write",
            tool_requirements=tuple(
                ToolRequirement(name=name, requirement="optional")
                for name in ("read", "ls", "find", "grep", "edit", "write", "bash")
            ),
        ),
        AgentDefinitionSource(
            definition_id="builtin_explorer",
            name="Explorer",
            role_prompt="Inspect the task context and report evidence. Do not modify the workspace.",
            model_selection="invoking_active",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="grep", requirement="optional"),
                ToolRequirement(name="write", requirement="forbidden"),
            ),
        ),
    )
