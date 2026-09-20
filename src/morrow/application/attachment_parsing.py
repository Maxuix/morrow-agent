"""Bounded, cancellable parsing independent of the Core command consumer."""

import asyncio
import base64
import json
import os
import sys

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.attachments import MAX_PRODUCT_BYTES


def _decode_part(item):
    mime, page, data, preview, *rest = item
    width = rest[0] if rest else None
    height = rest[1] if len(rest) > 1 else None
    return mime, page, base64.b64decode(data, validate=True), preview, width, height


class AttachmentParsePool:
    def __init__(self, *, concurrency=2, timeout=30):
        self.concurrency, self.timeout = concurrency, timeout
        self.active = 0
        self.reservations = set()

    async def parse(self, content, media_type):
        if self.active >= self.concurrency:
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Attachment parsers are busy; retry shortly"
            )
        self.active += 1
        process = None
        launch = None
        tasks = []
        try:
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "morrow.adapters.attachment_parser",
                    media_type,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                )
            )
            process = await asyncio.shield(launch)

            async def write():
                process.stdin.write(content)
                await process.stdin.drain()
                process.stdin.close()

            async def read():
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    if len(output) + len(chunk) > MAX_PRODUCT_BYTES * 2:
                        raise ValueError("Parser output exceeds limit")
                    output.extend(chunk)
                return bytes(output)

            tasks = [asyncio.create_task(write()), asyncio.create_task(read())]
            async with asyncio.timeout(self.timeout):
                _, output = await asyncio.gather(*tasks)
                await process.wait()
            if process.returncode != 0:
                raise ValueError("Invalid file")
            result = json.loads(output)
            result["parts"] = [_decode_part(item) for item in result["parts"]]
            if sum(len(p[2]) for p in result["parts"]) > MAX_PRODUCT_BYTES:
                raise ValueError("Parser output exceeds limit")
            return result
        except asyncio.CancelledError:
            raise
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "File is damaged, unsupported, or exceeds parsing limits; remove or retry",
            ) from None
        finally:

            async def reap():
                nonlocal process
                if process is None and launch is not None:
                    try:
                        process = await launch
                    except Exception:
                        pass
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if process is not None:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    await process.wait()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.active -= 1

            cleanup = asyncio.create_task(reap())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # A second cancellation cannot free an occupied parser slot.
                    continue
            cleanup.result()
