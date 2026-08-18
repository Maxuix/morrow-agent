"""Workspace-scoped find/search orchestration."""

from __future__ import annotations

import json

from morrow.adapters.local.search import LocalSearchAdapter, SearchAdapterError
from morrow.core.local_tools import SearchQuery, SearchTextResult
from morrow.services.files import (
    MAX_RESULT_BYTES,
    LocalFileError,
    WorkspaceFileService,
)


class WorkspaceSearchService:
    def __init__(
        self,
        files: WorkspaceFileService,
        *,
        adapter: LocalSearchAdapter | None = None,
    ) -> None:
        self.files = files
        self.adapter = adapter or LocalSearchAdapter()

    def find_files(self, *args, **kwargs):
        return self.files.find_files(*args, **kwargs)

    def search_text(
        self,
        path: str,
        *,
        query: SearchQuery,
        result_limit: int = MAX_RESULT_BYTES,
    ) -> SearchTextResult:
        resolved = self.files.resolver.resolve_directory(path)
        try:
            scan = self.adapter.search(
                workspace_root=self.files.resolver.root,
                search_root=resolved.target,
                relative_root=resolved.relative_path,
                query=query,
                sensitive_policy=self.files.sensitive_policy,
            )
        except SearchAdapterError as exc:
            raise LocalFileError(exc.code, exc.message) from exc
        result = SearchTextResult(
            path=resolved.relative_path,
            pattern=query.pattern,
            matches=scan.matches,
            engine=scan.engine,
            truncated=scan.truncated,
            budget_reason=scan.budget_reason,
            protected_paths=scan.protected_paths,
        )
        return self._fit(result, result_limit)

    @staticmethod
    def _fit(result: SearchTextResult, result_limit: int) -> SearchTextResult:
        matches = list(result.matches)
        protected = list(result.protected_paths)
        if not matches:
            if (
                len(
                    json.dumps(
                        result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                    )
                )
                <= result_limit
            ):
                return result
        while matches:
            candidate = result.model_copy(
                update={
                    "matches": tuple(matches),
                    "protected_paths": tuple(protected),
                    "truncated": result.truncated or len(matches) < len(result.matches),
                    "budget_reason": (
                        result.budget_reason
                        or ("result_budget" if len(matches) < len(result.matches) else None)
                    ),
                }
            ).model_dump(mode="json")
            if (
                len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")))
                <= result_limit
            ):
                return result.model_copy(
                    update={
                        "matches": tuple(matches),
                        "protected_paths": tuple(protected),
                        "truncated": result.truncated or len(matches) < len(result.matches),
                        "budget_reason": (
                            result.budget_reason
                            or ("result_budget" if len(matches) < len(result.matches) else None)
                        ),
                    }
                )
            matches.pop()
        while protected:
            candidate = result.model_copy(
                update={
                    "matches": (),
                    "protected_paths": tuple(protected),
                    "truncated": True,
                    "budget_reason": "result_budget",
                }
            )
            if (
                len(
                    json.dumps(
                        candidate.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                    )
                )
                <= result_limit
            ):
                return candidate
            protected.pop()
        candidate = result.model_copy(
            update={
                "matches": (),
                "protected_paths": (),
                "truncated": True,
                "budget_reason": "result_budget",
            }
        )
        if (
            len(
                json.dumps(
                    candidate.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                )
            )
            > result_limit
        ):
            raise LocalFileError("output_budget", "搜索结果无法放入当前预算")
        return candidate
