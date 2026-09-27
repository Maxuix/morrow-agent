"""Run one G1 Harbor fixture against a local scripted OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
RUNS = BENCH / "runs" / "g1"


def _has_completed_run(path: Path) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("kind") == "run.completed":
            return True
    return False


class ScriptedHandler(BaseHTTPRequestHandler):
    fixture_task = "g1-neutral"
    tool_results: list[str] = []

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"object": "list", "data": [{"id": "g1-scripted", "object": "model"}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        for message in request.get("messages", []):
            if message.get("role") == "tool":
                content = message.get("content")
                if isinstance(content, str) and content not in self.tool_results:
                    self.tool_results.append(content[:2000])
        tool_messages = [
            message for message in request.get("messages", []) if message.get("role") == "tool"
        ]
        tool_needed = (self.fixture_task == "g1-service" and len(tool_messages) < 3) or (
            self.fixture_task in {"g1-kill", "g1-interrupt"} and not tool_messages
        )
        arguments = {
            "mode": "start",
            "lifecycle": "acceptance",
            "command": "mkdir -p /tmp/g1-service && printf 'G1_SERVICE_OK' "
            "> /tmp/g1-service/index.html; "
            "exec /opt/morrow/venv/bin/python -m http.server 18765 "
            "--bind 127.0.0.1 --directory /tmp/g1-service",
        }
        if self.fixture_task == "g1-kill":
            arguments = {"mode": "foreground", "command": "kill -KILL $PPID"}
        elif self.fixture_task == "g1-interrupt":
            arguments = {"mode": "foreground", "command": "kill -INT $PPID"}
        if len(tool_messages) == 1:
            try:
                last_result = json.loads(tool_messages[-1]["content"])
                arguments = {
                    "mode": "poll",
                    "execution_id": last_result["result"]["execution_id"],
                }
            except (KeyError, TypeError, json.JSONDecodeError):
                tool_needed = False
        elif len(tool_messages) == 2:
            arguments = {
                "mode": "foreground",
                "command": "/opt/morrow/venv/bin/python -c 'import http.client; "
                'c=http.client.HTTPConnection("127.0.0.1",18765,timeout=5); '
                'c.request("GET","/"); '
                "r=c.getresponse(); print(r.status, r.read().decode())'",
            }
        if self.fixture_task in {"g1-kill", "g1-interrupt"}:
            tool_call_id = f"call_{self.fixture_task.replace('-', '_')}"
        elif len(tool_messages) == 2:
            tool_call_id = "call_g1_probe"
        elif tool_messages:
            tool_call_id = "call_g1_poll"
        else:
            tool_call_id = "call_g1_service"
        tool_call = {
            "index": 0,
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "bash",
                "arguments": json.dumps(arguments),
            },
        }
        if request.get("stream"):
            first_delta = (
                {"role": "assistant", "tool_calls": [tool_call]}
                if tool_needed
                else {"role": "assistant", "content": "Acknowledged."}
            )
            chunks = [
                {
                    "id": "chatcmpl-g1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "g1-scripted",
                    "choices": [
                        {
                            "index": 0,
                            "delta": first_delta,
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-g1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "g1-scripted",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "tool_calls" if tool_needed else "stop",
                        }
                    ],
                },
                {
                    "id": "chatcmpl-g1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "g1-scripted",
                    "choices": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                },
            ]
            body = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            )
            content_type = "text/event-stream"
        else:
            body = json.dumps(
                {
                    "id": "chatcmpl-g1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "g1-scripted",
                    "choices": [
                        {
                            "index": 0,
                            "message": (
                                {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            key: value
                                            for key, value in tool_call.items()
                                            if key != "index"
                                        }
                                    ],
                                }
                                if tool_needed
                                else {"role": "assistant", "content": "Acknowledged."}
                            ),
                            "finish_reason": "tool_calls" if tool_needed else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                }
            )
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task",
        choices=("g1-neutral", "g1-service", "g1-kill", "g1-interrupt"),
        default="g1-neutral",
    )
    args = parser.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    ScriptedHandler.fixture_task = args.task
    ScriptedHandler.tool_results = []
    server = ThreadingHTTPServer(("0.0.0.0", 0), ScriptedHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    job_name = f"{args.task}-{uuid.uuid4().hex[:12]}"
    output = RUNS / f"{job_name}.log"
    endpoint = f"http://host.docker.internal:{server.server_port}/v1"
    cmd = [
        str(BENCH / ".venv" / "bin" / "harbor"),
        "run",
        "-p",
        str(BENCH / "fixtures"),
        "-i",
        args.task,
        "-a",
        "harness.morrow_harbor_agent:MorrowAgent",
        "-o",
        str(RUNS / "jobs"),
        "--job-name",
        job_name,
        "--n-attempts",
        "1",
        "--max-retries",
        "0",
        "-n",
        "1",
        "-y",
        "--ak",
        "provider_adapter=openai-compatible",
        "--ak",
        f"provider_base_url={endpoint}",
        "--ak",
        "provider_id=bench",
        "--ak",
        "model_id=g1-scripted",
        "--ak",
        "api_model_id=g1-scripted",
        "--ak",
        "context_window_tokens=65536",
        "--ak",
        "max_output_tokens=1000",
        "--ak",
        "reasoning_effort=high",
    ]
    env = {
        **os.environ,
        "MORROW_BENCH_API_KEY": "scripted-placeholder",
        "PYTHONPATH": str(BENCH) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    try:
        with output.open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False, timeout=900
            )
    finally:
        server.shutdown()
        server.server_close()
    job = RUNS / "jobs" / job_name
    trials = [path for path in job.glob("*/result.json") if path.is_file()]
    valid = False
    reward = None
    partial_usage = None
    terminal_usage = None
    if len(trials) == 1:
        trial = json.loads(trials[0].read_text(encoding="utf-8"))
        agent_dir = trials[0].parent / "agent"
        run_log = agent_dir / "morrow-run.jsonl"
        reward = ((trial.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if args.task == "g1-kill":
            partial_file = agent_dir / "morrow-partial-metrics.json"
            partial_usage = (
                json.loads(partial_file.read_text(encoding="utf-8"))
                if partial_file.is_file()
                else None
            )
            valid = (
                bool(trial.get("exception_info"))
                and reward == 1.0
                and run_log.is_file()
                and not _has_completed_run(run_log)
                and partial_usage is not None
                and (
                    partial_usage.get("known_input_tokens", 0)
                    + partial_usage.get("known_output_tokens", 0)
                    > 0
                    or partial_usage.get("unknown_request_count", 0) > 0
                )
                and (agent_dir / "morrow-fingerprint.json").is_file()
            )
        elif args.task == "g1-interrupt":
            metrics_file = agent_dir / "morrow-terminal-metrics.json"
            metrics = (
                json.loads(metrics_file.read_text(encoding="utf-8"))
                if metrics_file.is_file()
                else {}
            )
            terminal_usage = metrics.get("usage")
            valid = (
                bool(trial.get("exception_info"))
                and reward == 1.0
                and run_log.is_file()
                and _has_completed_run(run_log)
                and metrics.get("finish_reason") == "cancelled"
                and metrics.get("execution_finished") is False
                and (terminal_usage or {}).get("total_tokens", 0) > 0
                and (agent_dir / "morrow-fingerprint.json").is_file()
            )
        else:
            valid = (
                not trial.get("exception_info")
                and reward == 1.0
                and run_log.is_file()
                and _has_completed_run(run_log)
                and (agent_dir / "morrow-terminal-metrics.json").is_file()
                and (agent_dir / "morrow-fingerprint.json").is_file()
            )
    print(
        json.dumps(
            {
                "job": str(job),
                "harbor_exit": proc.returncode,
                "trial_results": len(trials),
                "verifier_reward": reward,
                "partial_usage": partial_usage,
                "terminal_usage": terminal_usage,
                "tool_results": ScriptedHandler.tool_results if args.task == "g1-service" else [],
                "contract_passed": proc.returncode == 0 and valid,
                "log": str(output),
            }
        )
    )
    return 0 if proc.returncode == 0 and valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
