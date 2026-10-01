"""Local window picker state; no grant, native identity, or model history is stored here."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime

from pydantic import Field, ValidationError, field_validator

from morrow.application.computer_requests import ComputerUseSelection
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.computer_use import (
    MAX_APPS,
    MAX_DISCOVERED_TARGETS,
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    LocalComputerUseCandidates,
)
from morrow.core.domain import COMPUTER_SELECTION_ID_PREFIX
from morrow.core.models import ProtocolModel
from morrow.core.runtime_policy import ComputerUseMode
from morrow.runtime.policy import resolve_computer_use_settings

MAX_LOCAL_PICKERS = 8


class LocalWindowSelectionRequest(ProtocolModel):
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_DISCOVERED_TARGETS)
    allow_action: bool = Field(default=False, strict=True)
    share_images: bool = Field(default=False, strict=True)
    delivery: ComputerUseDelivery

    @field_validator("delivery", mode="before")
    @classmethod
    def delivery_value(cls, value):
        return ComputerUseDelivery(value) if isinstance(value, str) else value


@dataclass(frozen=True, slots=True, repr=False)
class _PendingSelection:
    selection_id: str
    selection: ComputerUseSelection
    expires_at: datetime
    claimed_key: str | None = None


class ComputerUseSelectionService:
    def __init__(self, application, lifecycle, settings_service, clock):
        self.application, self.lifecycle = application, lifecycle
        self.settings_service, self.clock = settings_service, clock
        self._catalogs = OrderedDict()
        self._selections = OrderedDict()

    def _settings(self, permission):
        if permission != "full-access-manual":
            raise ApplicationError(ApplicationErrorCode.INVALID, "桌面选择需要完整访问（逐次确认）")
        config = self.application.global_store.load().value
        if config is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "桌面配置需要修复")
        settings = resolve_computer_use_settings(config.runtime_policy)
        if not settings.enabled:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "桌面功能已关闭")
        return settings

    @staticmethod
    def _refused(exc):
        code = (
            ApplicationErrorCode.BUSY
            if exc.code == "desktop_busy"
            else ApplicationErrorCode.STALE
            if exc.code in {"stale_observation", "unknown_target"}
            else ApplicationErrorCode.UNAVAILABLE
        )
        raise ApplicationError(code, f"桌面选择不可用（{exc.code}）") from None

    async def prepare_catalog(self, *, permission):
        settings = self._settings(permission)
        try:
            return await self.lifecycle.discover_local_candidates(
                settings, authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
        except ComputerUseContractError as exc:
            self._refused(exc)

    def accept_catalog(self, session_id, catalog, *, permission):
        self._settings(permission)
        if (
            not isinstance(catalog, LocalComputerUseCandidates)
            or self.clock.now() >= catalog.expires_at
        ):
            raise ApplicationError(ApplicationErrorCode.STALE, "候选窗口已过期，请重新读取")
        self._catalogs[session_id] = catalog
        self._catalogs.move_to_end(session_id)
        self._selections.pop(session_id, None)
        while len(self._catalogs) > MAX_LOCAL_PICKERS:
            old, _ = self._catalogs.popitem(last=False)
            self._selections.pop(old, None)
        return catalog.model_dump(mode="json")

    def select(self, session_id, request, *, permission, model):
        settings = self._settings(permission)
        catalog = self._catalogs.get(session_id)
        if catalog is None or self.clock.now() >= catalog.expires_at:
            self._catalogs.pop(session_id, None)
            self._selections.pop(session_id, None)
            raise ApplicationError(ApplicationErrorCode.STALE, "候选窗口已过期，请重新读取")
        if not isinstance(request, LocalWindowSelectionRequest):
            raise ApplicationError(ApplicationErrorCode.INVALID, "窗口选择无效")
        try:
            request = LocalWindowSelectionRequest.model_validate(request.model_dump(), strict=True)
        except ValidationError:
            raise ApplicationError(ApplicationErrorCode.INVALID, "窗口选择无效") from None
        allowed = {item.candidate_id: item for item in catalog.candidates}
        if len(set(request.candidate_ids)) != len(request.candidate_ids) or any(
            item not in allowed for item in request.candidate_ids
        ):
            raise ApplicationError(ApplicationErrorCode.INVALID, "窗口不属于当前会话的候选列表")
        view = self.settings_service.view(model)
        if not view["model_capabilities"]["function_tools"]:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "当前模型不支持所需工具协议")
        if request.share_images and (
            settings.mode is not ComputerUseMode.HYBRID or not view["model_capabilities"]["images"]
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "窗口图像分享需要混合模式与图像模型"
            )
        apps = tuple(
            sorted(
                {allowed[item].app for item in request.candidate_ids}, key=lambda app: app.bundle_id
            )
        )
        if len(apps) > MAX_APPS:
            raise ApplicationError(ApplicationErrorCode.INVALID, "选择的应用数量超出限制")
        # Validate the application limit before allocating any owner bindings.
        selection = ComputerUseSelection(
            apps=apps,
            operations=(ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION)
            if request.allow_action
            else (ComputerUseOperation.OBSERVE,),
            delivery=request.delivery,
            image_share=ComputerUseImageShare.CONTROLLED_WINDOW
            if request.share_images
            else ComputerUseImageShare.NONE,
        )
        try:
            windows = self.lifecycle.select_local_candidates(
                request.candidate_ids, authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
        except ComputerUseContractError as exc:
            self._refused(exc)
        selection = ComputerUseSelection.model_validate(
            selection.model_dump() | {"windows": windows}, strict=True
        )
        selection_id = self.application.id_source.new_id(COMPUTER_SELECTION_ID_PREFIX)
        self._selections[session_id] = _PendingSelection(
            selection_id, selection, catalog.expires_at
        )
        return {
            "selection_id": selection_id,
            "expires_at": catalog.expires_at.isoformat(),
            "windows": [allowed[item].model_dump(mode="json") for item in request.candidate_ids],
            "operations": [item.value for item in selection.operations],
            "delivery": selection.delivery.value,
            "image_share": selection.image_share.value,
            "applies_to": "one_future_run",
        }

    def claim(self, session_id, selection_id, key, *, permission, model):
        settings = self._settings(permission)
        pending = self._selections.get(session_id)
        if (
            pending is None
            or pending.selection_id != selection_id
            or self.clock.now() >= pending.expires_at
        ):
            raise ApplicationError(ApplicationErrorCode.STALE, "本地窗口选择需要重新绑定")
        if pending.claimed_key not in {None, key}:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "窗口选择已绑定另一条输入")
        view = self.settings_service.view(model)
        if not view["model_capabilities"]["function_tools"]:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "当前模型不支持所需工具协议")
        if pending.selection.image_share is ComputerUseImageShare.CONTROLLED_WINDOW and (
            settings.mode is not ComputerUseMode.HYBRID or not view["model_capabilities"]["images"]
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "窗口图像分享需要混合模式与图像模型"
            )
        self._selections[session_id] = replace(pending, claimed_key=key)

    def release_claim(self, session_id, selection_id, key):
        pending = self._selections.get(session_id)
        if (
            pending is not None
            and pending.selection_id == selection_id
            and pending.claimed_key == key
        ):
            self._selections[session_id] = replace(pending, claimed_key=None)

    def consume(self, session_id, selection_id, *, key=None):
        pending = self._selections.get(session_id)
        if pending is None or pending.selection_id != selection_id:
            raise ApplicationError(ApplicationErrorCode.STALE, "本地窗口选择需要重新绑定")
        if pending.claimed_key is not None and pending.claimed_key != key:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "窗口选择已绑定另一条输入")
        self._selections.pop(session_id)
        if self.clock.now() >= pending.expires_at:
            raise ApplicationError(ApplicationErrorCode.STALE, "本地窗口选择已过期，请重新选择")
        return pending.selection
