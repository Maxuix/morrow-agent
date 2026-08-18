"""Fixed, bounded, non-interactive Git subprocess adapter."""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class GitAdapterError(RuntimeError):
    """Stable infrastructure failure from the read-only Git adapter."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GitCommandOutput:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool
    duration_ms: int


class GitInspectionAdapter:
    """Run only service-supplied Git argv with a scrubbed environment."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        max_output_bytes: int = 512 * 1024,
        termination_grace_seconds: float = 0.5,
    ) -> None:
        self.executable = executable or shutil.which("git")
        self.max_output_bytes = max_output_bytes
        self.termination_grace_seconds = termination_grace_seconds

    def run(
        self,
        root: Path,
        args: tuple[str, ...],
        *,
        timeout_seconds: float = 10.0,
    ) -> GitCommandOutput:
        if not self.executable:
            raise GitAdapterError("git_unavailable", "找不到 Git 可执行文件")
        if self.max_output_bytes < 1 or timeout_seconds <= 0:
            raise GitAdapterError("git_failed", "Git 执行预算无效")
        command = (self.executable, *args)
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(root),
                env=self._environment(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
                close_fds=True,
            )
        except OSError as exc:
            raise GitAdapterError("git_unavailable", "Git 进程无法启动") from exc

        selector = selectors.DefaultSelector()
        buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = False
        terminated = False
        try:
            assert process.stdout is not None
            assert process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    self._terminate(process)
                    terminated = True
                    raise GitAdapterError("git_timeout", "Git 检查超时")
                events = selector.select(remaining)
                if not events:
                    self._terminate(process)
                    terminated = True
                    raise GitAdapterError("git_timeout", "Git 检查超时")
                for key, _ in events:
                    stream = key.fileobj
                    try:
                        chunk = os.read(stream.fileno(), 64 * 1024)
                    except OSError as exc:
                        self._terminate(process)
                        terminated = True
                        raise GitAdapterError("git_failed", "Git 输出读取失败") from exc
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    buffer = buffers[key.data]
                    remaining_bytes = self.max_output_bytes - len(buffer)
                    if remaining_bytes > 0:
                        buffer.extend(chunk[:remaining_bytes])
                    if len(chunk) > max(0, remaining_bytes):
                        truncated = True
                        self._terminate(process)
                        terminated = True
                        selector.unregister(stream)
                        for registered in tuple(selector.get_map().values()):
                            selector.unregister(registered.fileobj)
                        break
                if terminated:
                    break
        finally:
            selector.close()
            if not terminated:
                try:
                    process.wait(timeout=max(0.1, timeout_seconds))
                except subprocess.TimeoutExpired as exc:
                    self._terminate(process)
                    raise GitAdapterError("git_timeout", "Git 检查超时") from exc
            else:
                try:
                    process.wait(timeout=max(0.1, self.termination_grace_seconds + 0.2))
                except subprocess.TimeoutExpired as exc:
                    raise GitAdapterError("git_timeout", "Git 进程清理超时") from exc
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        return GitCommandOutput(
            returncode=process.returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
            truncated=truncated,
            duration_ms=min(120_000, max(0, int((time.monotonic() - started) * 1000))),
        )

    @staticmethod
    def _environment(root: Path) -> dict[str, str]:
        """Build an allowlist; Git must not inherit user config or credentials."""

        false_executable = "/usr/bin/false" if Path("/usr/bin/false").exists() else "false"
        return {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C",
            "LC_ALL": "C",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_ASKPASS": false_executable,
            "SSH_ASKPASS": false_executable,
            "GIT_EDITOR": false_executable,
            "GIT_SEQUENCE_EDITOR": false_executable,
        }

    def _terminate(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (OSError, ProcessLookupError):
            return
        try:
            process.wait(timeout=self.termination_grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            return
        try:
            process.wait(timeout=self.termination_grace_seconds)
        except subprocess.TimeoutExpired as exc:
            raise GitAdapterError("git_timeout", "Git 进程清理超时") from exc
