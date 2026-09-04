"""Task-specialized, suggestion-only Drafts through the existing Compiler and Draft writer."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, ValidationError

from morrow.application.workflows.planning_catalog import PlanningCatalogService
from morrow.application.workflows.planning_features import local_features, merge_classification
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.models import ProtocolModel
from morrow.core.orchestration import GraphPlanningRequest, PlannerExplanation, PlannerMetadata
from morrow.core.workflows.contracts import (
    ContractRef,
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    TaskContractRef,
    WorkflowInputBinding,
)
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.core.workflows.drafts import WorkflowDraftView


class TaskGraphDraft(ProtocolModel):
    workflow_draft: WorkflowDraftView | None
    metadata: PlannerMetadata | None = None
    explanation: PlannerExplanation
    diagnostics: tuple[str, ...] = Field(default=(), max_length=16)


def effective_budget(policy, request):
    """Explicit caps can narrow each other; missing limits never become guessed budgets."""
    values = {}
    for field in (
        "max_agent_generation_requests",
        "default_node_max_agent_generation_requests",
        "admission_timeout_seconds",
    ):
        limits = [
            getattr(value, field)
            for value in (policy, request)
            if value is not None and getattr(value, field) is not None
        ]
        values[field] = min(limits) if limits else None
    return WorkflowBudget(**values)


@dataclass(frozen=True)
class PreparedGraphPlan:
    request: GraphPlanningRequest
    digest: str
    features: object
    brief: object
    classification: str
    diagnostics: tuple[str, ...]


class GraphPlannerService:
    def __init__(self, drafts, policies, *, classifier=None, scout=None, workspace_constraints=()):
        self.drafts = drafts
        self.policies = policies
        self.catalogs = PlanningCatalogService(drafts)
        self.classifier = classifier
        self.scout = scout
        self.workspace_constraints = workspace_constraints

    async def generate(self, request: GraphPlanningRequest) -> TaskGraphDraft:
        return self.save_prepared(await self.prepare(request))

    def _existing(self, request, digest):
        existing = self.drafts.get(request.draft_id)
        if existing is None:
            return None
        metadata = existing.draft.planner
        if metadata is None or metadata.request_digest != digest:
            raise ValueError("planner Draft ID conflicts with another request")
        return TaskGraphDraft(
            workflow_draft=existing,
            metadata=metadata,
            explanation=metadata.explanation,
            diagnostics=metadata.diagnostics,
        )

    async def prepare(self, request: GraphPlanningRequest):
        """Read-only and awaitable. HTTP runs this outside the mutation bus."""
        digest = sha256_digest(canonical_json_bytes(request.model_dump(mode="json")))
        existing = self._existing(request, digest)
        if existing is not None:
            return existing
        features = local_features(request, workspace_constraints=self.workspace_constraints)
        diagnostics = []
        brief = None
        if request.scout:
            if self.scout is None:
                diagnostics.append("scout_unavailable: workspace directory read is not authorized")
            else:
                try:
                    brief = self.scout.inspect()
                except Exception:
                    diagnostics.append("scout_unavailable: project metadata could not be inspected")
        classification = "local"
        if request.use_model:
            if self.classifier is None:
                classification = "unavailable"
            else:
                try:
                    result = await self.classifier.classify(request.task, features, brief)
                    features = merge_classification(features, result, request)
                    classification = "model"
                except ValueError:
                    classification = "invalid"
                except Exception:
                    classification = "unavailable"
            if classification != "model":
                diagnostics.append(f"classification_{classification}: local task features used")
        return PreparedGraphPlan(
            request, digest, features, brief, classification, tuple(diagnostics)
        )

    def save_prepared(self, prepared) -> TaskGraphDraft:
        """Synchronous compilation and sole Draft write on the serialized command bus."""
        if isinstance(prepared, TaskGraphDraft):
            return prepared
        request, digest = prepared.request, prepared.digest
        existing = self._existing(request, digest)
        if existing is not None:
            return existing
        features, brief = prepared.features, prepared.brief
        classification, diagnostics = prepared.classification, list(prepared.diagnostics)
        # Read policy/catalogs after the Provider await; no model classification can change them.
        policy = self.policies.resolve(features.task_type)
        budget = effective_budget(policy.budget_limits, request.budget)
        required = set(policy.required_roles) | set(features.user_requested_roles)
        excluded = set(policy.excluded_roles) | set(features.user_excluded_roles)
        if policy.review_requirement == "required":
            required.add("reviewer")
        if policy.review_requirement == "skip":
            excluded.add("reviewer")
        reasons = [
            f"task_type={features.task_type}; areas={features.number_of_areas}; risk={features.risk_level}; review={features.review_value}"
        ]
        if required & excluded:
            diagnostics.append(
                "role_conflict: requested roles are excluded; edit the request or policy"
            )
            return self._needs_input(policy, budget, reasons, diagnostics)
        complexity = features.number_of_areas >= 3 or features.expected_duration_class == "long"
        risk = features.risk_level == "high" and features.review_value != "low"
        read_benefit = features.parallelizable_read_work and features.number_of_areas >= 2
        multi = policy.multi_agent and (complexity or risk or read_benefit or bool(required))
        if required == {"direct"}:
            multi = False
        if policy.preferred_template == "direct" and not required:
            multi = False
            reasons.append("User policy prefers Direct.")
        if not policy.multi_agent and required - {"direct"}:
            diagnostics.append("policy_conflict: required roles need multi-agent enabled")
            return self._needs_input(policy, budget, reasons, diagnostics)
        if multi:
            roles = self._roles(
                features,
                required,
                excluded,
                explore_prior=policy.preferred_template == "explore_implement_verify",
            )
            cap = budget.max_agent_generation_requests
            if cap is not None and cap < len(roles):
                reasons.append(
                    "Explicit request guardrail is smaller than the candidate node count."
                )
                if required:
                    diagnostics.append(
                        "guardrail_conflict: increase the explicit cap or remove required roles"
                    )
                    return self._needs_input(policy, budget, reasons, diagnostics)
                multi = False
        if not multi:
            roles = [("direct", None)]
            reasons.append(
                "Direct-first: the task or explicit user policy does not justify extra nodes."
            )
        else:
            reasons.append(
                "Complexity, independent review value or separable read work justifies this graph."
            )
        if not multi and ("direct" in excluded or "direct" in policy.excluded_templates):
            diagnostics.append("direct_excluded: supplement the task or change the explicit policy")
            return self._needs_input(policy, budget, reasons, diagnostics)
        starting = "direct" if not multi else "grammar"
        if multi and policy.preferred_template == "explore_implement_verify":
            starting = "explore_implement_verify"
            reasons.append(
                "Explore–Implement–Verify is a structural prior; nodes and bindings are task-specific."
            )
        source = None
        candidate = None
        # Initial compile + exactly one deterministic regeneration from fresh authorities.
        for attempt in range(2):
            try:
                source = self._compose(
                    request, features, policy, budget, roles, self.catalogs.nodes()
                )
            except ValidationError:
                diagnostics.append(
                    "structure_invalid: task facts exceed the Compiler source schema; narrow scope or roles"
                )
                source = None
                break
            except ValueError as exc:
                # Only fixed planning diagnostics cross this boundary.
                diagnostics.append(str(exc)[:512])
                source = None
                break
            candidate, errors = self._compile(source)
            if candidate is not None:
                break
            diagnostics.extend(errors)
            if attempt == 0:
                reasons.append(
                    "Compiler rejected the first Draft; regenerated once against current Catalogs."
                )
        if (
            candidate is None
            and multi
            and not required
            and "direct" not in excluded
            and "direct" not in policy.excluded_templates
        ):
            try:
                source = self._compose(
                    request, features, policy, budget, [("direct", None)], self.catalogs.nodes()
                )
                candidate, errors = self._compile(source)
                diagnostics.extend(errors)
                if candidate is not None:
                    multi, starting = False, "direct"
                    reasons.append(
                        "The specialized graph could not compile; a validated Direct Draft is available."
                    )
            except ValidationError:
                diagnostics.append(
                    "structure_invalid: Direct source exceeds the Compiler schema; narrow task facts"
                )
            except ValueError as exc:
                diagnostics.append(str(exc)[:512])
        if candidate is None or source is None:
            return self._needs_input(policy, budget, reasons, diagnostics)
        explanation = PlannerExplanation(
            mode="multi" if multi else "direct",
            reasons=tuple(reasons),
            starting_point=starting,
            node_count=len(source.nodes),
            writing_nodes=tuple(n.node_id for n in source.nodes if n.access_mode == "write"),
            models=tuple(
                {
                    (
                        n.resolved_model_ref.provider_id,
                        n.resolved_model_ref.model_id,
                    ): n.resolved_model_ref
                    for n in candidate.nodes
                }.values()
            ),
            budget=budget,
            auto_run_reason="paired_evidence_missing"
            if policy.auto_run_mode == "allow_promoted"
            else "approval_only",
        )
        metadata = PlannerMetadata(
            request_digest=digest,
            source_hash=source.content_hash,
            features=features,
            brief=brief,
            policy_id=policy.policy_id,
            policy_scope=policy.scope,
            policy_revision=policy.revision,
            classification=classification,
            explanation=explanation,
            diagnostics=tuple(dict.fromkeys(diagnostics))[:16],
        )
        document = self.drafts.management.workflow_sources.load(self.drafts.workspace_id)
        view = self.drafts.create(
            source,
            expected_source_revision=document.revision,
            draft_id=request.draft_id,
            planner=metadata,
        )
        return TaskGraphDraft(
            workflow_draft=view,
            metadata=metadata,
            explanation=explanation,
            diagnostics=metadata.diagnostics,
        )

    def _compile(self, source):
        result, diagnostics = self.drafts.validate(source)
        errors = tuple(f"{d.code}: {d.message}"[:512] for d in diagnostics if d.severity == "error")
        return (result.candidate if not errors else None), errors

    @staticmethod
    def _roles(features, required, excluded, *, explore_prior=False):
        roles = []
        if (
            features.requires_research
            or features.number_of_areas > 1
            or "explorer" in required
            or explore_prior
        ):
            focuses = features.expected_scope if features.parallelizable_read_work else ()
            roles.extend(("explorer", focus) for focus in (focuses or (None,)))
        if (
            features.task_type == "refactor"
            or features.number_of_areas >= 5
            or features.ambiguity == "high"
            or "planner" in required
        ):
            roles.append(("planner", None))
        if features.requires_code_write and "direct" not in required:
            roles.append(("coder", None))
        for role in sorted(required - {r for r, _ in roles} - {"reviewer", "synthesizer"}):
            roles.append((role, None))
        if (
            features.review_value != "low"
            or features.risk_level == "high"
            or "reviewer" in required
        ):
            roles.append(("reviewer", None))
        if not features.requires_code_write or "synthesizer" in required:
            roles.append(("synthesizer", None))
        return [(role, focus) for role, focus in roles if role not in excluded]

    def _compose(self, request, features, policy, budget, roles, entries):
        if not roles or len(roles) > 16:
            raise ValueError("graph_size: choose between 1 and 16 permitted roles")
        nodes, edges = [], []
        explorers = []
        previous = None
        counts = {}
        excluded = set(policy.excluded_roles) | set(features.user_excluded_roles)
        tool_access = self.drafts.management.agent_publication.catalog.tool_access
        for role, focus in roles:
            counts[role] = counts.get(role, 0) + 1
            node_id = f"{role}_{counts[role]}" if sum(r == role for r, _ in roles) > 1 else role
            access = (
                "write" if features.requires_code_write and role in {"coder", "direct"} else "read"
            )
            preferred_model = policy.model_preferences_by_role.get(
                role, self.drafts.management.active_model
            )
            candidates = [
                e
                for e in entries
                if e.model == preferred_model
                and (access == "read" or e.version.source.access_mode_ceiling == "write")
                and e.version.source.definition_id.removeprefix("builtin_") not in excluded
                and e.version.source.name.casefold() not in excluded
                and (
                    access == "write"
                    or not any(
                        t.requirement == "required" and tool_access.get(t.name) != "read"
                        for t in e.version.source.tool_requirements
                    )
                )
            ]
            exact = [
                e
                for e in candidates
                if e.version.source.definition_id in {role, f"builtin_{role}"}
                or e.version.source.name.casefold() == role
            ]
            if role not in {"direct", "explorer", "planner", "coder", "reviewer", "synthesizer"}:
                candidates = exact
            else:
                candidates = sorted(
                    candidates,
                    key=lambda e: (
                        e not in exact,
                        e.version.source.access_mode_ceiling != access,
                        e.version.source.definition_id,
                    ),
                )
            if not candidates:
                raise ValueError(
                    f"catalog_missing: publish an enabled {role} Agent with the authorized model, Skills and {access} ceiling"
                )
            entry = candidates[0]
            parents = []
            if role == "explorer" and previous is None:
                pass
            elif previous is not None:
                parents = [previous]
            elif explorers:
                parents = list(explorers)
            # Separable exploration branches join at the next ordinary node.
            if role != "explorer" and explorers and previous is None:
                parents = list(explorers)
            bindings = [
                WorkflowInputBinding(
                    source="workflow_input",
                    input_name="task",
                    accepts=TaskContractRef(),
                    workflow_input="task",
                )
            ]
            for parent in parents:
                edges.append(WorkflowEdge(from_node_id=parent, to_node_id=node_id))
                bindings.append(
                    NodeOutputBinding(
                        source="node_output",
                        input_name=f"input_{parent}",
                        accepts=ContractRef(kind="TextResult"),
                        node_output=NodeOutputRef(node_id=parent, output_slot="result"),
                    )
                )
            objectives = {
                "explorer": "Collect evidence and identify relevant constraints",
                "planner": "Plan the change and validation steps",
                "coder": "Implement and verify the requested change",
                "reviewer": "Independently review correctness, risks and validation evidence",
                "synthesizer": "Synthesize the supplied evidence into the requested result",
                "direct": "Complete the requested task",
            }
            objective = (
                f"{objectives.get(role, 'Perform the requested role')}"
                + (f" for {focus}" if focus else "")
                + f". Task: {request.task.objective}"
            )
            constraints = tuple(
                dict.fromkeys((*request.task.constraints, *features.workspace_constraints))
            )
            if len(constraints) > 32:
                raise ValueError(
                    "constraint_capacity: combine task and workspace constraints into at most 32 entries"
                )
            nodes.append(
                AgentNodeSource(
                    node_id=node_id,
                    agent_definition_ref=entry.ref,
                    task_contract=TaskContract(
                        objective=objective[:4096],
                        scope=(focus,) if focus else features.expected_scope,
                        constraints=constraints,
                        source_refs=request.task.source_refs,
                    ),
                    input_bindings=tuple(bindings),
                    output_contracts=(OutputContract(slot="result"),),
                    access_mode=access,
                    conversation_scope="invoking_session"
                    if role == "direct" and len(roles) == 1
                    else "isolated",
                    max_agent_generation_requests=budget.default_node_max_agent_generation_requests,
                )
            )
            if role == "explorer" and previous is None:
                explorers.append(node_id)
            else:
                previous = node_id
        return WorkflowDefinitionSource(
            workflow_definition_id=request.workflow_definition_id,
            name=request.name,
            description=f"Task-specialized {features.task_type} Draft; review and edit before running.",
            nodes=tuple(nodes),
            edges=tuple(edges),
            default_budget=budget,
            required_outputs=(NodeOutputRef(node_id=nodes[-1].node_id, output_slot="result"),),
        )

    @staticmethod
    def _needs_input(policy, budget, reasons, diagnostics):
        return TaskGraphDraft(
            workflow_draft=None,
            explanation=PlannerExplanation(
                mode="needs_input",
                reasons=tuple(reasons),
                starting_point="grammar",
                node_count=0,
                budget=budget,
                auto_run_reason="paired_evidence_missing"
                if policy.auto_run_mode == "allow_promoted"
                else "approval_only",
            ),
            diagnostics=tuple(dict.fromkeys(diagnostics))[:16],
        )
