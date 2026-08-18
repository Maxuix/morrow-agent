"""Native sandbox backend contracts and platform-specific argv/rule builders."""

from __future__ import annotations

import asyncio
import platform
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError, ProcessOutput
from morrow.services.sandbox import SandboxChangeSet, SandboxServiceError, SandboxSnapshotService


@dataclass(frozen=True)
class SandboxCapability:
    """A conservative capability probe; degraded backends are never usable."""

    platform: str
    backend: str
    supported: bool
    reason: str
    executable: str | None = None
    copy_on_write: bool = False


class SandboxBackendError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SandboxBackend:
    name = "unsupported"

    def probe(self) -> SandboxCapability:
        raise NotImplementedError

    def build_command(
        self,
        *,
        argv: tuple[str, ...],
        snapshot_root: Path,
        temp_root: Path,
        private_home: Path,
        cwd: Path,
        blocked_paths: tuple[Path, ...] = (),
    ) -> tuple[str, ...]:
        raise NotImplementedError


class NativeSandboxProcessAdapter:
    """Adapt one native backend to the common bounded process adapter contract."""

    def __init__(
        self,
        workspace_root: Path,
        snapshots: SandboxSnapshotService,
        backend: SandboxBackend,
        *,
        process: HostProcessAdapter | None = None,
        prepare_timeout_seconds: float = 15.0,
        collect_timeout_seconds: float = 15.0,
        cleanup_timeout_seconds: float = 10.0,
    ) -> None:
        if min(prepare_timeout_seconds, collect_timeout_seconds, cleanup_timeout_seconds) <= 0:
            raise ValueError("sandbox phase timeouts must be positive")
        self.workspace_root = workspace_root.resolve(strict=True)
        self.snapshots = snapshots
        self.backend = backend
        self.process = process or HostProcessAdapter()
        self.prepare_timeout_seconds = prepare_timeout_seconds
        self.collect_timeout_seconds = collect_timeout_seconds
        self.cleanup_timeout_seconds = cleanup_timeout_seconds
        self.last_change_set: SandboxChangeSet | None = None
        self.run_identity = ("sandbox-process", "sandbox-call")

    async def run(
        self,
        *,
        argv: tuple[str, ...] | None,
        shell: str | None,
        cwd: Path,
        timeout_seconds: float,
        environment: dict[str, str],
        output_limit: int,
        redaction_overlap: int = 0,
    ) -> ProcessOutput:
        self.last_change_set = None
        try:
            relative_cwd = cwd.resolve(strict=True).relative_to(self.workspace_root)
        except (OSError, ValueError) as exc:
            raise ProcessAdapterError(
                "sandbox_unavailable", "沙箱工作目录不在冻结工作空间内"
            ) from exc
        session = None
        temp_root = None
        collected = False
        try:
            temp_root = self.snapshots.reserve_temp_root()
            prepare_cancel = threading.Event()
            session = await self._snapshot_phase(
                lambda: self.snapshots.prepare(
                    self.workspace_root,
                    run_id=self.run_identity[0],
                    call_id=self.run_identity[1],
                    temp_root=temp_root,
                    cancel_event=prepare_cancel,
                ),
                timeout_seconds=self.prepare_timeout_seconds,
                cancel_event=prepare_cancel,
            )
            command = ("/bin/sh", "-c", shell) if shell is not None else argv
            if not command:
                raise ProcessAdapterError("invalid_command", "沙箱命令为空")
            sandbox_cwd = session.snapshot_root / relative_cwd
            sandbox_command = self.backend.build_command(
                argv=tuple(command),
                snapshot_root=session.snapshot_root,
                temp_root=session.private_temp,
                private_home=session.private_home,
                cwd=sandbox_cwd,
                blocked_paths=(session.source_root,),
            )
            sandbox_environment = dict(environment)
            sandbox_environment.update(
                {
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(session.private_home),
                    "TMPDIR": str(session.private_temp),
                    "XDG_CACHE_HOME": str(session.private_cache),
                }
            )
            output = await self.process.run(
                argv=sandbox_command,
                shell=None,
                cwd=sandbox_cwd,
                timeout_seconds=min(timeout_seconds, 75.0),
                environment=sandbox_environment,
                output_limit=output_limit,
                redaction_overlap=redaction_overlap,
            )
            collect_cancel = threading.Event()
            self.last_change_set = await self._snapshot_phase(
                lambda: self.snapshots.collect(session, cancel_event=collect_cancel),
                timeout_seconds=self.collect_timeout_seconds,
                cancel_event=collect_cancel,
            )
            collected = True
            return output
        except asyncio.CancelledError:
            if session is not None and not collected:
                try:
                    collect_cancel = threading.Event()
                    self.last_change_set = await asyncio.shield(
                        self._snapshot_phase(
                            lambda: self.snapshots.collect(session, cancel_event=collect_cancel),
                            timeout_seconds=self.collect_timeout_seconds,
                            cancel_event=collect_cancel,
                        )
                    )
                except Exception:
                    pass
            raise
        except (SandboxServiceError, SandboxBackendError) as exc:
            raise ProcessAdapterError(exc.code, exc.message) from exc
        except TimeoutError as exc:
            raise ProcessAdapterError("sandbox_timeout", "沙箱阶段超时") from exc
        finally:
            if session is not None:
                try:
                    await asyncio.shield(
                        asyncio.wait_for(
                            asyncio.to_thread(self.snapshots.cleanup, session),
                            self.cleanup_timeout_seconds,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except (SandboxServiceError, TimeoutError) as exc:
                    raise ProcessAdapterError(
                        "sandbox_cleanup_failed", "沙箱临时目录清理失败"
                    ) from exc
            elif temp_root is not None:
                try:
                    await asyncio.shield(
                        asyncio.wait_for(
                            asyncio.to_thread(self.snapshots.cleanup_reserved, temp_root),
                            self.cleanup_timeout_seconds,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except (SandboxServiceError, TimeoutError) as exc:
                    raise ProcessAdapterError(
                        "sandbox_cleanup_failed", "沙箱临时目录清理失败"
                    ) from exc

    async def _snapshot_phase(
        self,
        operation,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ):
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout_seconds)
        except (TimeoutError, asyncio.CancelledError):
            cancel_event.set()
            await asyncio.shield(self._settle_snapshot_phase(task))
            raise

    async def _settle_snapshot_phase(self, task: asyncio.Task) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), self.cleanup_timeout_seconds)
        except TimeoutError as exc:
            raise ProcessAdapterError("sandbox_cleanup_failed", "沙箱后台阶段未能及时停止") from exc
        except Exception:
            pass


class UnsupportedSandboxBackend(SandboxBackend):
    name = "unsupported"

    def __init__(self, reason: str = "当前平台没有可用原生沙箱后端") -> None:
        self.reason = reason

    def probe(self) -> SandboxCapability:
        return SandboxCapability(
            platform=platform.system().casefold() or "unknown",
            backend=self.name,
            supported=False,
            reason=self.reason,
        )

    def build_command(self, **kwargs) -> tuple[str, ...]:
        del kwargs
        raise SandboxBackendError("sandbox_unavailable", self.reason)


class MacOSSeatbeltBackend(SandboxBackend):
    name = "macos-seatbelt"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("sandbox-exec")

    def probe(self) -> SandboxCapability:
        if platform.system() != "Darwin":
            return SandboxCapability(
                platform=platform.system().casefold() or "unknown",
                backend=self.name,
                supported=False,
                reason="Seatbelt 后端只在 macOS 上支持",
                executable=self.executable,
            )
        if not self.executable:
            return SandboxCapability(
                platform="darwin",
                backend=self.name,
                supported=False,
                reason="找不到 sandbox-exec",
            )
        return SandboxCapability(
            platform="darwin",
            backend=self.name,
            supported=True,
            reason="sandbox-exec 可用",
            executable=self.executable,
            copy_on_write=True,
        )

    def build_command(
        self,
        *,
        argv: tuple[str, ...],
        snapshot_root: Path,
        temp_root: Path,
        private_home: Path,
        cwd: Path,
        blocked_paths: tuple[Path, ...] = (),
    ) -> tuple[str, ...]:
        capability = self.probe()
        if not capability.supported or not self.executable:
            raise SandboxBackendError("sandbox_unavailable", capability.reason)
        profile = self._profile(
            snapshot_root, temp_root, private_home, cwd, blocked_paths=blocked_paths
        )
        return (self.executable, "-p", profile, "--", *argv)

    @staticmethod
    def _profile(
        snapshot_root: Path,
        temp_root: Path,
        private_home: Path,
        cwd: Path,
        *,
        blocked_paths: tuple[Path, ...] = (),
    ) -> str:
        """Build a default-deny profile for the resolved workspace and Host data roots."""

        del cwd
        blocked = (Path.home(), *blocked_paths)
        blocked_rules = " ".join(
            f'(deny file-read* (subpath "{path}"))' for path in dict.fromkeys(blocked)
        )
        writable = " ".join(
            f'(subpath "{path}")' for path in (snapshot_root, temp_root, private_home)
        )
        return (
            "(version 1) "
            "(deny default) "
            "(allow process*) "
            "(allow sysctl-read) "
            "(allow mach-lookup) "
            "(allow file-read*) "
            '(allow file-read-metadata (subpath "/")) '
            f"(allow file-write* {writable}) "
            f"{blocked_rules} "
            "(deny network*)"
        )


class LinuxBubblewrapBackend(SandboxBackend):
    name = "linux-bubblewrap"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("bwrap")

    def probe(self) -> SandboxCapability:
        if platform.system() != "Linux":
            return SandboxCapability(
                platform=platform.system().casefold() or "unknown",
                backend=self.name,
                supported=False,
                reason="bubblewrap 后端只在 Linux 上支持",
                executable=self.executable,
            )
        if not self.executable:
            return SandboxCapability(
                platform="linux",
                backend=self.name,
                supported=False,
                reason="找不到 bubblewrap",
            )
        return SandboxCapability(
            platform="linux",
            backend=self.name,
            supported=False,
            reason="bubblewrap 规则已实现，但尚未通过真实 Linux runner 验收",
            executable=self.executable,
            copy_on_write=False,
        )

    def build_command(
        self,
        *,
        argv: tuple[str, ...],
        snapshot_root: Path,
        temp_root: Path,
        private_home: Path,
        cwd: Path,
        blocked_paths: tuple[Path, ...] = (),
    ) -> tuple[str, ...]:
        capability = self.probe()
        if not capability.supported or not self.executable:
            raise SandboxBackendError("sandbox_unavailable", capability.reason)
        return self._build_command(
            executable=self.executable,
            argv=argv,
            snapshot_root=snapshot_root,
            temp_root=temp_root,
            private_home=private_home,
            cwd=cwd,
            blocked_paths=blocked_paths,
        )

    @staticmethod
    def _build_command(
        *,
        executable: str,
        argv: tuple[str, ...],
        snapshot_root: Path,
        temp_root: Path,
        private_home: Path,
        cwd: Path,
        blocked_paths: tuple[Path, ...] = (),
    ) -> tuple[str, ...]:
        # Only explicit bind mounts are visible; blocked Host paths are never mounted.
        del blocked_paths
        system_roots = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")
        command = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-network",
            "--unshare-pid",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        for root in system_roots:
            if Path(root).exists():
                command.extend(("--ro-bind", root, root))
        command.extend(
            (
                "--bind",
                str(snapshot_root),
                str(snapshot_root),
                "--bind",
                str(temp_root),
                str(temp_root),
                "--bind",
                str(private_home),
                str(private_home),
                "--chdir",
                str(cwd),
                "--",
                *argv,
            )
        )
        return tuple(command)


def default_sandbox_backend() -> SandboxBackend:
    system = platform.system()
    if system == "Darwin":
        return MacOSSeatbeltBackend()
    if system == "Linux":
        return LinuxBubblewrapBackend()
    return UnsupportedSandboxBackend(f"{system or 'unknown'} 不在首版原生沙箱支持范围内")
