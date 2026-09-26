"""Bounded, evidence-based review before accepting a coding task's final answer."""

from __future__ import annotations

import re
from dataclasses import dataclass

from morrow.core.capabilities import (
    ChangeToolFact,
    ToolRunContext,
    ValidationFact,
    active_tracked_executions,
    validation_evidence_stale,
)

MAX_COMPLETION_REVIEWS = 2
_ACCEPTANCE_CUES = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+|\b[\w.-]+\.(?:py|js|ts|tsx|html|css|json|ya?ml|csv|md|txt|xml|pdf)\b"
    r"|\b(?:json|yaml|csv|xml|html|pytest|unittest|latency|throughput|percent|milliseconds)\b"
    r"|%|毫秒|秒|性能|测试|格式|单位|服务|运行状态|端口|输出路径",
    re.IGNORECASE,
)


def has_acceptance_cues(user_input: str) -> bool:
    """Find explicit public delivery conditions without interpreting hidden tests."""

    return bool(_ACCEPTANCE_CUES.search(user_input[:4000]))


@dataclass(frozen=True)
class CompletionCheck:
    validation_outcome: str
    issues: tuple[str, ...]
    evidence: tuple[str, ...]


def check_completion(run: ToolRunContext) -> CompletionCheck:
    """Use fact order, not a validator's last status in isolation.

    A passing check predating a later file change is no longer proof for the
    delivered version. No validator fact is treated as proof of the whole goal.
    """

    latest: dict[tuple[str, str], tuple[int, ValidationFact]] = {}
    historical: dict[tuple[str, str], ValidationFact] = {}
    facts = run.facts
    for index, fact in enumerate(facts):
        if isinstance(fact, ValidationFact):
            if fact.historical:
                historical[(fact.validator_kind, fact.scope)] = fact
            else:
                latest[(fact.validator_kind, fact.scope)] = (index, fact)
    issues: list[str] = []
    evidence: list[str] = []
    if active_tracked_executions(facts):
        issues.append("后台命令仍在运行，可能继续改变工作区；请等待终态并重新验证")
    changed_paths = dict.fromkeys(
        path
        for fact in facts
        if isinstance(fact, ChangeToolFact) and fact.status not in {"failed", "rejected"}
        for path in fact.relative_paths
    )
    if changed_paths:
        evidence.append("已记录的文件变更：" + "、".join(tuple(changed_paths)[:16]))
    # A re-read of an earlier run's settled result stays visible, but only a
    # validation executed against the current version can decide the outcome.
    for (kind, scope), fact in sorted(historical.items()):
        evidence.append(
            f"{kind} ({scope}) 的历史结果：{fact.status}（先前执行的回读，不代表当前版本）"
        )
    statuses: list[str] = []
    for (kind, scope), (index, fact) in sorted(latest.items()):
        if validation_evidence_stale(facts, index):
            issues.append(f"{kind} ({scope}) 的结果早于后续变更或不透明命令，需要重新验证最终版本")
            continue
        statuses.append(fact.status)
        evidence.append(f"{kind} ({scope})：{fact.status}")
        if fact.status != "passed":
            issues.append(f"{kind} ({scope}) 最近一次验证为 {fact.status}")
    if any(status in {"failed", "inconclusive", "timeout", "cancelled"} for status in statuses):
        validation = "failed"
    elif issues:
        validation = "not_run"
    elif statuses:
        validation = "passed"
    else:
        validation = "not_run"
    return CompletionCheck(
        validation_outcome=validation,
        issues=tuple(issues[:8]),
        evidence=tuple(evidence[:17]),
    )


def completion_review_prompt(
    user_input: str,
    check: CompletionCheck,
    *,
    candidate_text: str,
    final: bool,
) -> str:
    """Transient model input; never a second ConversationLog writer."""

    request = " ".join(user_input.split())[:1200]
    candidate = " ".join(candidate_text.split())[:1200]
    issues = "；".join(check.issues)[:1200] if check.issues else "当前没有已记录的失败验证"
    evidence = (
        "；".join(check.evidence)[:1600] if check.evidence else "没有可用的文件变更或验证事实"
    )
    close = (
        "这是最后一次自动复核机会。能修正就修正；无法验证的项目明确写为未验证。"
        if final
        else "发现缺口时先修正并验证；没有可运行测试时如实写明未验证。"
    )
    return (
        "交付前复核（仅依据当前用户任务与公开工具事实，不代表隐藏评测结果）："
        f"用户目标：{request}。"
        f"拟交付答案：{candidate}。"
        "逐项核对要求的输出路径、格式和单位、运行状态、公开测试及性能条件；"
        "检查最后一次修改之后的验证，并保留任务所需服务和文件。"
        f"公开工具证据：{evidence}。"
        f"已知验证缺口：{issues}。{close}"
    )
