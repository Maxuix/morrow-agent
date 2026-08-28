/** S7P-09 Pi policy: workspace-only file tools and Seatbelt-confined project commands. */

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { type BashOperations, createBashTool } from "@earendil-works/pi-coding-agent";

const TOOL_TIMEOUT_SECONDS = 120;
const BLOCKED_COMMANDS = [
	/(?:^|\s)(?:curl|wget|ssh|scp|nc|ncat|telnet)\b/i,
	/(?:^|\s)(?:sudo|su)\b/i,
	/(?:^|\s)git\s+(?:add|am|apply|bisect|branch|checkout|cherry-pick|clean|commit|fetch|gc|merge|mv|pull|push|rebase|reset|restore|revert|rm|stash|switch|tag|worktree)\b/i,
	/(?:^|\s)(?:npm|pnpm|yarn|pip|pip3|uv)\s+(?:add|install|sync|update)\b/i,
];

function seatbeltQuote(value: string): string {
	return value.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function isInside(root: string, candidate: string): boolean {
	const rel = relative(root, candidate);
	return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

function confinedPath(root: string, value: unknown): boolean {
	if (typeof value !== "string" || value.length === 0 || value.includes("\0")) return false;
	const normalized = value.startsWith("@") ? value.slice(1) : value;
	return isInside(root, resolve(root, normalized));
}

function sandboxedBashOperations(root: string): BashOperations {
	return {
		async exec(command, cwd, { onData, signal, timeout }) {
			if (!isInside(root, realpathSync(cwd))) throw new Error("evaluation_policy:cwd_outside_workspace");
			const privateTemp = mkdtempSync(join(tmpdir(), "morrow-pi-s7p09-"));
			const profile = [
				"(version 1)",
				"(deny default)",
				"(allow process*)",
				"(allow sysctl-read)",
				"(allow mach-lookup)",
				"(allow file-read*)",
				`(deny file-read* (subpath "${seatbeltQuote(process.env.HOME ?? "/nonexistent")}"))`,
				`(allow file-read* (subpath "${seatbeltQuote(root)}"))`,
				`(deny file-write* (subpath "${seatbeltQuote(join(root, ".git"))}"))`,
				`(allow file-write* (subpath "${seatbeltQuote(root)}") (subpath "${seatbeltQuote(privateTemp)}"))`,
				"(deny network*)",
			].join(" ");
			const effectiveTimeout = Math.min(timeout ?? TOOL_TIMEOUT_SECONDS, TOOL_TIMEOUT_SECONDS);
			return await new Promise((resolveExecution, reject) => {
				const child = spawn(
					"/usr/bin/sandbox-exec",
					["-p", profile, "--", "/bin/bash", "-c", command],
					{
						cwd,
						detached: true,
						env: { ...process.env, HOME: privateTemp, TMPDIR: privateTemp },
						stdio: ["ignore", "pipe", "pipe"],
					},
				);
				let timedOut = false;
				const stop = () => {
					if (!child.pid) return;
					try {
						process.kill(-child.pid, "SIGKILL");
					} catch {
						child.kill("SIGKILL");
					}
				};
				const timer = setTimeout(() => {
					timedOut = true;
					stop();
				}, effectiveTimeout * 1000);
				const abort = () => stop();
				signal?.addEventListener("abort", abort, { once: true });
				child.stdout?.on("data", onData);
				child.stderr?.on("data", onData);
				child.on("error", (error) => {
					clearTimeout(timer);
					signal?.removeEventListener("abort", abort);
					reject(error);
				});
				child.on("close", (code) => {
					clearTimeout(timer);
					signal?.removeEventListener("abort", abort);
					if (signal?.aborted) reject(new Error("aborted"));
					else if (timedOut) reject(new Error("timeout:120"));
					else resolveExecution({ exitCode: code });
				});
			});
		},
	};
}

export default function evaluationPolicy(pi: ExtensionAPI) {
	const root = realpathSync(process.cwd());
	const blockedCallIds = new Set<string>();
	if (!existsSync("/usr/bin/sandbox-exec")) throw new Error("evaluation_policy:sandbox_unavailable");
	const bash = createBashTool(root, { operations: sandboxedBashOperations(root) });
	pi.registerTool({ ...bash, label: "bash (S7P-09 confined)" });

	pi.on("tool_call", (event) => {
		if (["read", "write", "edit"].includes(event.toolName)) {
			const input = event.input as Record<string, unknown>;
			const candidate = input.path ?? input.file_path;
			if (!confinedPath(root, candidate)) {
				blockedCallIds.add(event.toolCallId);
				return { block: true, reason: "evaluation_policy:path_outside_workspace", terminate: true };
			}
		}
		if (event.toolName === "bash") {
			const input = event.input as { command?: unknown; timeout?: unknown };
			if (typeof input.command !== "string" || BLOCKED_COMMANDS.some((rule) => rule.test(input.command))) {
				blockedCallIds.add(event.toolCallId);
				return { block: true, reason: "evaluation_policy:prohibited_command", terminate: true };
			}
			input.timeout = Math.min(
				typeof input.timeout === "number" ? input.timeout : TOOL_TIMEOUT_SECONDS,
				TOOL_TIMEOUT_SECONDS,
			);
		}
		return undefined;
	});

	pi.on("tool_result", (event) => {
		const input = event.input as Record<string, unknown>;
		const policyBlocked = blockedCallIds.delete(event.toolCallId);
		const effectiveWrite = ["write", "edit"].includes(event.toolName) && !event.isError;
		const command = typeof input.command === "string" ? input.command : "";
		const validator = /(?:^|\s)(?:pytest|py\.test)(?:\s|$)/i.test(command)
			? "pytest"
			: /(?:^|\s)ruff(?:\s|$)/i.test(command)
				? "ruff"
				: command.includes("compileall")
					? "compileall"
					: undefined;
		return {
			details: {
				...(typeof event.details === "object" && event.details !== null ? event.details : {}),
				evaluation: {
					terminal_state: policyBlocked ? "denied" : event.isError ? "failed" : "succeeded",
					effective_write: effectiveWrite,
					validation_status: validator ? (event.isError ? "failed" : "passed") : undefined,
					invalid_arguments: false,
					basic_tool_blocked: false,
				},
			},
		};
	});
}
