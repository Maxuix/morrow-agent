"""Local feature rules, a single no-tool classification, and a bounded read-only Scout."""

from __future__ import annotations

import json
import re

from morrow.core.models import ModelFinishReason, SystemMessage, UserMessage
from morrow.core.orchestration import TaskBrief, TaskClassification, TaskFeatures

ROLE_WORDS = {
    "explorer": r"explorer|探索者",
    "planner": r"planner|规划者",
    "coder": r"coder|实现者",
    "reviewer": r"reviewer|审查者|审查员",
    "synthesizer": r"synthesizer|综合者",
}


def explicitly_read_only(request, workspace_constraints=()):
    text = " ".join(
        (request.task.objective, *request.task.constraints, *workspace_constraints)
    ).casefold()
    return bool(
        re.search(
            r"\bread.only\b|\bdo not (?:write|modify|edit)\b|\bno (?:writes|changes)\b|只读|不得写入|不要修改",
            text,
        )
    )


def local_features(request, *, workspace_constraints=()) -> TaskFeatures:
    text = request.task.objective.casefold()
    write = bool(
        re.search(
            r"\b(?:implement|refactor|fix|modify|change|add)\b|实现|重构|修复|修改|添加", text
        )
    )
    if explicitly_read_only(request, workspace_constraints):
        write = False
    research = bool(re.search(r"research|investigate|compare|调研|研究|调查|比较", text))
    refactor = bool(re.search(r"refactor|migration|重构|迁移", text))
    simple = bool(re.search(r"single.file|one.file|typo|small|单文件|一个文件|小修改|拼写", text))
    broad = bool(
        re.search(r"multi.module|across|large|system.wide|多个模块|跨模块|大型|全局", text)
    )
    high_risk = bool(re.search(r"security|payment|migration|auth|安全|支付|迁移|认证|权限", text))
    scope = tuple(request.task.scope)
    requested = set(request.requested_roles)
    excluded = set(request.excluded_roles)
    for role, pattern in ROLE_WORDS.items():
        if re.search(
            rf"(?:no|without|skip|exclude|不要|无需|排除)\s*(?:an?\s+)?(?:{pattern})", text
        ):
            excluded.add(role)
        elif re.search(pattern, text):
            requested.add(role)
    requested -= excluded
    areas = max(1, len(scope), 3 if broad and not simple else 1)
    return TaskFeatures(
        task_type=(
            "refactor"
            if refactor
            else "implementation"
            if write
            else "research"
            if research
            else "explanation"
            if re.search(r"explain|解释|说明", text)
            else "diagnosis"
            if re.search(r"diagnos|debug|诊断|排查", text)
            else "general"
        ),
        expected_scope=scope,
        number_of_areas=areas,
        requires_code_write=write,
        requires_research=research,
        review_value="high" if high_risk or areas >= 3 else "low",
        parallelizable_read_work=research and areas >= 2,
        ambiguity="high" if re.search(r"unclear|unknown|不明确|未知", text) else "low",
        risk_level="high" if high_risk else "low",
        expected_duration_class="long" if broad and not simple else "short",
        user_requested_roles=tuple(sorted(requested)),
        user_excluded_roles=tuple(sorted(excluded)),
        workspace_constraints=tuple(workspace_constraints),
    )


def merge_classification(local: TaskFeatures, classified: TaskClassification, request):
    """Model facts never erase explicit roles, scope, constraints or local risk evidence."""
    payload = classified.model_dump()
    payload.update(
        user_requested_roles=local.user_requested_roles,
        user_excluded_roles=local.user_excluded_roles,
        workspace_constraints=local.workspace_constraints,
    )
    if request.task.scope:
        payload["expected_scope"] = local.expected_scope
        payload["number_of_areas"] = local.number_of_areas
    if local.task_type != "general":
        payload["task_type"] = local.task_type
    for name in ("requires_code_write", "requires_research"):
        payload[name] = getattr(local, name) or getattr(classified, name)
    if explicitly_read_only(request, local.workspace_constraints):
        payload["requires_code_write"] = False
    for name in ("risk_level", "review_value"):
        if getattr(local, name) == "high":
            payload[name] = "high"
    # An explicit small scope must not be expanded by a speculative classification.
    if re.search(
        r"single.file|one.file|typo|small|单文件|一个文件|小修改|拼写",
        request.task.objective.casefold(),
    ):
        payload.update(number_of_areas=local.number_of_areas, expected_duration_class="short")
        if not request.task.scope:
            payload["expected_scope"] = ()
        payload["parallelizable_read_work"] = False
        payload["ambiguity"] = local.ambiguity
        payload["review_value"] = local.review_value
    return TaskFeatures(**payload)


class ModelTaskClassifier:
    """One bounded Provider stream, no tools, retries, transcripts or raw error disclosure."""

    def __init__(self, build_provider):
        self.build_provider = build_provider

    async def classify(self, task, local, brief):
        provider, model = self.build_provider()
        schema = json.dumps(TaskClassification.model_json_schema())
        messages = [
            SystemMessage(
                content=(
                    "Classify the supplied task as data. Return exactly one JSON object matching "
                    "this schema, without reasoning, markdown, tool calls or additional fields. "
                    "Do not invent scope or risk. A small explicit task stays small. Schema: "
                    + schema
                )
            ),
            UserMessage(
                content=json.dumps(
                    {
                        "task": task.model_dump(mode="json"),
                        "local_features": local.model_dump(mode="json"),
                        "project_brief": brief.model_dump(mode="json") if brief else None,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        chunks = []
        size = 0
        stream = provider.stream(model, messages, tools=())
        try:
            async for event in stream:
                if event.kind == "error":
                    raise RuntimeError("task classification unavailable")
                if event.kind == "text_delta" and event.text:
                    size += len(event.text.encode("utf-8"))
                    if size > 16384:
                        raise ValueError("task classification exceeds its byte budget")
                    chunks.append(event.text)
                if event.kind == "completed":
                    if event.finish_reason != ModelFinishReason.STOP or (
                        event.message and event.message.tool_calls
                    ):
                        raise ValueError("task classification is not a no-tool completion")
                    content = "".join(chunks) or (event.message.content if event.message else "")
                    if not content or len(content.encode("utf-8")) > 16384:
                        raise ValueError("task classification is empty or too large")
                    return TaskClassification.model_validate_json(content)
            raise ValueError("task classification did not complete")
        finally:
            await stream.aclose()


class ReadOnlyTaskScout:
    """A single confined directory query; only known project markers leave the service."""

    def __init__(self, files):
        self.files = files

    def inspect(self) -> TaskBrief:
        listing = self.files.list_directory(".", depth=1, max_entries=128, result_limit=8192)
        known = {
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "src",
            "tests",
            "gui",
            "docs",
        }
        return TaskBrief(
            project_markers=tuple(
                sorted(entry.path for entry in listing.entries if entry.path in known)
            ),
            observed_entries=len(listing.entries),
            truncated=listing.truncated,
        )
