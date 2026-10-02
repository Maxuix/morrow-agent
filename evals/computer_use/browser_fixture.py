"""Serve a controlled localhost form and independent state; no SDK or browser launch."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = b"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Morrow Local Browser Fixture</title>
<style>
body {font:18px system-ui;max-width:650px;margin:30px auto;padding:0 20px;background:#f5f7fa}
button,input {font:inherit;padding:8px;margin:5px 0}label {display:block;margin:15px 0}
#rows {height:180px;overflow:auto;border:2px solid #697482;background:white;padding:10px}
#rows p {margin:12px 0}output {display:block;color:#164825;white-space:pre-wrap}
</style>
<h1>Morrow controlled browser fixture</h1>
<p>Localhost only. Synthetic input only. No accounts or external resources.</p>
<p id="counter">Count: 0</p><button id="increment">Increment</button>
<label>Unicode text <input id="text" maxlength="2048" autocomplete="off"></label>
<output id="echo">Echo:</output>
<label>Synthetic secure field <input id="secure" type="password" autocomplete="off"></label>
<p>Secure bytes are never sent to the server or state file.</p>
<div id="rows" tabindex="0" aria-label="Fixture scroll rows"></div>
<p id="status" role="status">Loading independent state</p>
<script>
const byId = id => document.getElementById(id);
for(let n=0;n<40;n++){const row=document.createElement('p');row.textContent=`Fixture row ${n}`;byId('rows').append(row);}
let queue=Promise.resolve();
function display(state){byId('counter').textContent=`Count: ${state.count}`;
 byId('echo').textContent=`Echo: ${state.text}`;
 byId('status').textContent=`State output ready; revision ${state.revision}`;}
function event(kind,value){queue=queue.then(async()=>{
 const reply=await fetch('/events',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify(value===undefined?{kind}:{kind,value})});
 if(!reply.ok)throw Error('fixture_event_rejected');display(await reply.json());
 }).catch(()=>{byId('status').textContent='State output failed';});}
byId('increment').onclick=()=>event('increment');
byId('text').oninput=()=>event('text',byId('text').value);
byId('secure').oninput=()=>event('secure',byId('secure').value.length>0);
byId('rows').onscroll=()=>event('scroll',byId('rows').scrollTop);
fetch('/state').then(r=>r.json()).then(state=>{byId('text').value=state.text;display(state);})
 .catch(()=>{byId('status').textContent='State output failed';});
</script></html>"""


class FixtureInputError(ValueError):
    """A fixed fixture rejection; never contains request values."""


class FixtureState:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.directory = directory
        self.lock = threading.Lock()
        self.values = {
            "schema_version": 1,
            "instance_id": uuid.uuid4().hex,
            "pid": os.getpid(),
            "revision": 0,
            "count": 0,
            "text": "",
            "secure_field_populated": False,
            "scroll_offset": 0,
        }
        self._write(self.values)

    def _write(self, values: dict) -> None:
        content = json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.directory / "state.json")
            finally:
                temporary.unlink(missing_ok=True)

    def read(self) -> dict:
        with self.lock:
            return dict(self.values)

    def apply(self, payload: object) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"kind", "value"}:
            raise FixtureInputError("invalid_event")
        kind = payload.get("kind")
        value = payload.get("value")
        if kind == "increment":
            valid = set(payload) == {"kind"}
        elif kind == "text":
            valid = isinstance(value, str) and len(value.encode("utf-8")) <= 2048
        elif kind == "secure":
            valid = isinstance(value, bool)
        elif kind == "scroll":
            valid = type(value) in (int, float) and 0 <= value <= 100_000 and math.isfinite(value)
        else:
            valid = False
        if not valid:
            raise FixtureInputError("invalid_event")
        with self.lock:
            state = dict(self.values)
            if kind == "increment":
                state["count"] += 1
            else:
                state[
                    {"text": "text", "secure": "secure_field_populated", "scroll": "scroll_offset"}[
                        kind
                    ]
                ] = value
            state["revision"] += 1
            self._write(state)
            self.values = state
            return dict(state)


def handler_for(state: FixtureState):
    class FixtureHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log URL, inputs, headers or exception content.

        def reply(self, status: int, content: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def expected_host(self) -> str:
            return f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if self.headers.get("Host") != self.expected_host():
                self.reply(403, b'{"code":"fixture_host_required"}', "application/json")
            elif self.path == "/":
                self.reply(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/state":
                self.reply(200, json.dumps(state.read()).encode(), "application/json")
            else:
                self.reply(404, b'{"code":"fixture_path_required"}', "application/json")

        def do_POST(self):
            if (
                self.path != "/events"
                or self.headers.get("Host") != self.expected_host()
                or self.headers.get("Origin") != f"http://{self.expected_host()}"
                or self.headers.get("Content-Type") != "application/json"
            ):
                self.reply(403, b'{"code":"fixture_origin_required"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise FixtureInputError("invalid_event")
                payload = json.loads(self.rfile.read(length))
                result = state.apply(payload)
            except (ValueError, UnicodeError):
                self.reply(400, b'{"code":"invalid_event"}', "application/json")
                return
            except OSError:
                self.reply(500, b'{"code":"fixture_state_failed"}', "application/json")
                return
            self.reply(200, json.dumps(result).encode(), "application/json")

    return FixtureHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18788)
    args = parser.parse_args()
    state = FixtureState(args.state_directory.resolve())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(state))
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{server.server_port}/",
                "state_file": str(state.directory / "state.json"),
                "page_sha256": hashlib.sha256(PAGE).hexdigest(),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
