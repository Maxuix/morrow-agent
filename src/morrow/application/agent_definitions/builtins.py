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
                ToolRequirement(name="ls", requirement="optional"),
                ToolRequirement(name="find", requirement="optional"),
                ToolRequirement(name="write", requirement="forbidden"),
                ToolRequirement(name="edit", requirement="forbidden"),
                ToolRequirement(name="bash", requirement="forbidden"),
                ToolRequirement(name="promote_sandbox_changes", requirement="forbidden"),
            ),
        ),
        AgentDefinitionSource(
            definition_id="builtin_coder",
            name="Coder",
            role_prompt=(
                "Implement the requested change using structured edit/write tools and native-sandbox"
                " bash. Promote sandbox changes before finishing. Do not guess diffs from the"
                " workspace after the fact."
            ),
            model_selection="invoking_active",
            access_mode_ceiling="write",
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="edit", requirement="required"),
                ToolRequirement(name="write", requirement="required"),
                ToolRequirement(name="bash", requirement="required"),
                ToolRequirement(name="promote_sandbox_changes", requirement="optional"),
                ToolRequirement(name="grep", requirement="optional"),
                ToolRequirement(name="ls", requirement="optional"),
                ToolRequirement(name="find", requirement="optional"),
            ),
        ),
        AgentDefinitionSource(
            definition_id="builtin_reviewer",
            name="Reviewer",
            role_prompt=(
                "Review the task, change evidence and tests. Submit a ReviewReport verdict."
                " Do not modify the workspace."
            ),
            model_selection="invoking_active",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="grep", requirement="optional"),
                ToolRequirement(name="ls", requirement="optional"),
                ToolRequirement(name="find", requirement="optional"),
                ToolRequirement(name="write", requirement="forbidden"),
                ToolRequirement(name="edit", requirement="forbidden"),
                ToolRequirement(name="bash", requirement="forbidden"),
                ToolRequirement(name="promote_sandbox_changes", requirement="forbidden"),
            ),
        ),
        AgentDefinitionSource(
            definition_id="builtin_planner",
            name="Planner",
            role_prompt=(
                "Turn the task and Explorer evidence into a bounded implementation plan. Submit a"
                " PlanArtifact; do not modify the workspace."
            ),
            model_selection="invoking_active",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="optional"),
                ToolRequirement(name="grep", requirement="optional"),
                ToolRequirement(name="write", requirement="forbidden"),
                ToolRequirement(name="edit", requirement="forbidden"),
                ToolRequirement(name="bash", requirement="forbidden"),
            ),
        ),
        AgentDefinitionSource(
            definition_id="builtin_synthesizer",
            name="Synthesizer",
            role_prompt=(
                "Synthesize all supplied Explorer EvidenceBundles into one SynthesisReport."
                " Preserve source attribution and uncertainties; do not modify the workspace."
            ),
            model_selection="invoking_active",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="optional"),
                ToolRequirement(name="write", requirement="forbidden"),
                ToolRequirement(name="edit", requirement="forbidden"),
                ToolRequirement(name="bash", requirement="forbidden"),
            ),
        ),
    )
