"""Process-local ChangeSet projections for truthful mutation reporting."""

from __future__ import annotations

import json

from morrow.core.capabilities import ToolRunContext
from morrow.core.local_tools import ChangeSetResult, MutationResult


class ChangeSetService:
    """Retain bounded actual mutation results only for the active run."""

    def record(self, run: ToolRunContext, result: MutationResult) -> None:
        run.retain_change_set(result.change_set_id, result)

    def show(self, run: ToolRunContext, *, result_limit: int) -> ChangeSetResult:
        entries = [value for value in run.change_sets if isinstance(value, MutationResult)]
        result = ChangeSetResult(entries=tuple(entries))
        if _json_size(result) <= result_limit:
            return result
        bounded: list[MutationResult] = []
        for entry in entries:
            candidate = entry.model_copy(update={"diff": "", "diff_truncated": True})
            bounded.append(candidate)
            if _json_size(ChangeSetResult(entries=tuple(bounded))) > result_limit:
                bounded.pop()
                break
        result = ChangeSetResult(
            entries=tuple(bounded),
            truncated=len(bounded) < len(entries),
        )
        if _json_size(result) > result_limit:
            result = ChangeSetResult(entries=(), truncated=True)
        if _json_size(result) > result_limit:
            raise ValueError("change set result budget is too small")
        return result


def _json_size(value: ChangeSetResult) -> int:
    return len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
