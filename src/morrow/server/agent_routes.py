"""Agent preset preferences and quick-save: the bounded Chat-facing surface.

D04: preset identity, prompts and tool policy stay fixed; this module only
exposes the model/thinking preference overlay (independent non-sensitive YAML)
and the few-field custom save path. The full advanced surface stays on the
existing definition routes; quick saves are a distinct controlled endpoint, not
a second full source writer.
"""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.adapters.state.preset_preference_yaml import AgentPresetPreferenceYamlStore
from morrow.application.agent_definitions.preferences import AgentPresetPreferenceService
from morrow.application.agent_definitions.quick_save import (
    AgentQuickSaveFields,
    build_quick_save_source,
    derive_definition_id,
)
from morrow.core.agent_presets import (
    PRESET_DEFINITION_IDS,
    PRESET_ROLE_BY_ID,
    AgentPresetPreference,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.execution_selections import GenerationChoice
from morrow.core.models import ModelRef
from morrow.server.commands import ServerCommands
from morrow.server.protocol import CommandRequest


class PresetPreferenceWrite(CommandRequest):
    definition_id: str = Field(max_length=128)
    preference: AgentPresetPreference
    expected_revision: int = Field(ge=0, strict=True)


class AgentQuickSaveWrite(CommandRequest):
    name: str = Field(min_length=1, max_length=128)
    purpose: str = Field(default="", max_length=2048)
    prompt: str = Field(default="", max_length=32768)
    tools: Literal["all"] | tuple[str, ...] = "all"
    model: ModelRef | None = None
    generation: GenerationChoice | None = None
    expected_source_revision: int = Field(default=0, ge=0, strict=True)
    expected_head_revision: int = Field(default=0, ge=0, strict=True)


def preference_service(c) -> AgentPresetPreferenceService:
    """The Application service over the workspace preference YAML overlay."""

    def efforts(model):
        from morrow.core.agent_runs import exact_model_capabilities

        config = c.application.global_store.load().value.providers[model.provider_id]
        exact = exact_model_capabilities(
            config.adapter,
            c.application.registry.capabilities(config.adapter),
            model,
            config.models[model.model_id].capabilities,
        )
        return exact.reasoning_efforts

    return AgentPresetPreferenceService(
        AgentPresetPreferenceYamlStore(c.application.data_root.root),
        workspace_id=c.workspace_id,
        validator=efforts,
    )


def agent_routes(host, parse_body, context=None):
    def resolve(request):
        if context is not None:
            return context(request)
        workspace_id = request.path_params.get("workspace_id", host.context.workspace_id)
        registry = host.context.workspaces
        if registry is not None:
            return registry.get(workspace_id)
        if workspace_id != host.context.workspace_id:
            raise ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope")
        return host.context

    async def presets(request):
        c = resolve(request)

        def query():
            service = preference_service(c)
            document = service.document()
            commands = ServerCommands(c)
            rows = []
            for definition_id in PRESET_DEFINITION_IDS:
                try:
                    view = commands.catalog_agent_definition(definition_id)["agent_definition"]
                except ApplicationError:
                    view = None
                # Presets materialize without a desired YAML source; their
                # display facts come from the admission-proven exact version.
                published = (view or {}).get("published_version") or {}
                source = published.get("source") or (view or {}).get("source") or {}
                preference = document.preference_for(definition_id)
                rows.append(
                    {
                        "definition_id": definition_id,
                        "role": PRESET_ROLE_BY_ID[definition_id],
                        "name": source.get("name") or definition_id,
                        "description": source.get("description") or "",
                        "access": "write" if definition_id == "builtin_general" else "read",
                        "available": bool(
                            view
                            and view.get("head")
                            and view["head"].get("enabled")
                            and view.get("published_version")
                            and not view.get("revoked")
                        ),
                        "preference": preference.model_dump(mode="json", exclude={"definition_id"})
                        if preference
                        else None,
                        "preference_source": "preset_preference"
                        if preference
                        else "inherit_session",
                    }
                )
            return {"presets": rows, "revision": document.revision}

        return JSONResponse(await host.execute_query(query))

    async def preference(request):
        c = resolve(request)
        definition_id = request.path_params["definition_id"]
        body = await parse_body(request, PresetPreferenceWrite)
        if body.definition_id != definition_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Preference path and body identities differ"
            )
        if definition_id not in PRESET_DEFINITION_IDS:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Preferences apply only to the fixed workflow presets"
            )

        def apply_preference():
            commands = ServerCommands(c)

            def apply():
                document = preference_service(c).set(
                    definition_id, body.preference, expected_revision=body.expected_revision
                )
                return {
                    "definition_id": definition_id,
                    "revision": document.revision,
                    "preference": body.preference.model_dump(
                        mode="json", exclude={"definition_id"}
                    ),
                }, definition_id

            def rebuild(_):
                document = preference_service(c).document()
                preference = document.preference_for(definition_id)
                return {
                    "definition_id": definition_id,
                    "revision": document.revision,
                    "preference": preference.model_dump(mode="json", exclude={"definition_id"})
                    if preference
                    else None,
                }

            value, receipt = commands._idempotent(
                "agent_preset_preference",
                body.command_id,
                {
                    "definition_id": definition_id,
                    "expected_revision": body.expected_revision,
                    "preference": body.preference.model_dump(mode="json"),
                },
                apply,
                rebuild,
                result_kind="session",
            )
            return {**value, "receipt": receipt.model_dump(mode="json")}

        return JSONResponse(await host.execute_command(apply_preference))

    async def quick_save(request):
        c = resolve(request)
        body = await parse_body(request, AgentQuickSaveWrite)

        def apply_quick_save():
            commands = ServerCommands(c)

            def apply():
                definition_id = derive_definition_id(body.name)
                try:
                    source = build_quick_save_source(
                        AgentQuickSaveFields(
                            name=body.name,
                            purpose=body.purpose,
                            prompt=body.prompt,
                            tools=body.tools,
                            model=body.model,
                            generation=body.generation,
                        ),
                        existing=c.management._desired_agent_source(definition_id),
                        catalog=c.management.agent_publication.catalog,
                    )
                    result = c.management.save_and_publish_agent(
                        source,
                        expected_source_revision=body.expected_source_revision,
                        expected_head_revision=body.expected_head_revision,
                        command_id=body.command_id,
                    )
                except ValueError as exc:
                    # Receipt conflicts and empty custom tool lists surface as
                    # bounded user errors, never as unhandled 500s.
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT
                        if "conflict" in str(exc) or "receipt" in str(exc)
                        else ApplicationErrorCode.INVALID,
                        str(exc),
                    ) from None
                view = commands.catalog_agent_definition(result.definition_id)
                return {
                    "definition_id": result.definition_id,
                    "available_version_id": result.available_version_id,
                    "enabled": result.enabled,
                    "agent_definition": view["agent_definition"],
                }, result.definition_id

            value, receipt = commands._idempotent(
                "agent_quick_save",
                body.command_id,
                body.model_dump(mode="json", exclude={"command_id"}),
                apply,
                lambda receipt: (
                    lambda view: {
                        "definition_id": receipt.result_id,
                        # The receipt proves this exact published version is the
                        # admission-checked available version.
                        "available_version_id": (view.get("published_version") or {}).get(
                            "version_id"
                        ),
                        "enabled": bool((view.get("head") or {}).get("enabled")),
                        "agent_definition": view,
                    }
                )(commands.catalog_agent_definition(receipt.result_id)["agent_definition"]),
                result_kind="agent_definition",
            )
            return {**value, "receipt": receipt.model_dump(mode="json")}

        return JSONResponse(await host.execute_command(apply_quick_save))

    root = "/v1/workspaces/{workspace_id}/agent-presets"
    return [
        Route(root, presets),
        Route(root + "/{definition_id}", preference, methods=["PUT"]),
        Route("/v1/agent-definitions/quick-save", quick_save, methods=["POST"]),
    ]
