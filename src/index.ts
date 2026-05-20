import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type InputEvent = {
	text: string;
	source?: string;
	images?: unknown[];
};

type InputResult =
	| { action: "continue" }
	| { action: "transform"; text: string; images?: unknown[] }
	| { action: "handled" };

type CommandContext = {
	hasUI?: boolean;
	ui?: {
		notify?: (message: string, level?: "info" | "warning" | "error") => void;
	};
};

type ExtensionAPI = {
	on(
		event: "input",
		handler: (
			event: InputEvent,
			ctx?: CommandContext,
		) => Promise<InputResult> | InputResult,
	): void;
	registerCommand(
		name: string,
		command: {
			description: string;
			handler: (args: string, ctx: CommandContext) => Promise<void> | void;
		},
	): void;
	sendUserMessage(text: string): void;
};

type CleanerPayload = {
	ok?: boolean;
	status?: "cleaned" | "original" | "error";
	text?: string;
	reason?: string;
	mode?: string;
	original_chars?: number;
	cleaned_chars?: number;
	savings_ratio?: number;
	deleted?: Array<{ text: string; reason: string }>;
};

type State =
	| { kind: "idle" }
	| { kind: "loading"; startedAt: string }
	| { kind: "ready"; mode: string; checkedAt: string }
	| { kind: "failed"; reason: string; checkedAt: string };

const extensionDir = dirname(fileURLToPath(import.meta.url));
const defaultCleanerPath = resolve(extensionDir, "../python/prompt_cleaner.py");
const falseValues = new Set(["0", "false", "no", "off", "disabled"]);

function envFlag(name: string, fallback: boolean): boolean {
	const value = process.env[name];
	return value === undefined || value === ""
		? fallback
		: !falseValues.has(value.toLowerCase());
}

function envNumber(name: string, fallback: number): number {
	const value = process.env[name];
	if (!value) return fallback;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function settings() {
	return {
		python:
			process.env.PI_PROMPT_HELPER_PYTHON || process.env.PYTHON || "python3",
		cleaner: process.env.PI_PROMPT_HELPER_CLEANER || defaultCleanerPath,
		minSavings: envNumber("PI_PROMPT_HELPER_MIN_SAVINGS", 0.03),
		timeoutMs: envNumber("PI_PROMPT_HELPER_TIMEOUT_MS", 2000),
	};
}

function notify(
	ctx: CommandContext | undefined,
	message: string,
	level: "info" | "warning" | "error" = "info",
) {
	ctx?.ui?.notify?.(message, level);
}

function shouldSkip(event: InputEvent, enabled: boolean): boolean {
	if (!enabled) return true;
	if (event.source === "extension") return true;
	if (!event.text.trim()) return true;
	if (
		!envFlag("PI_PROMPT_HELPER_CLEAN_SLASH", false) &&
		event.text.trimStart().startsWith("/")
	)
		return true;
	if (
		!envFlag("PI_PROMPT_HELPER_CLEAN_IMAGES", false) &&
		event.images &&
		event.images.length > 0
	)
		return true;
	return false;
}

function runCleaner(args: string[], stdin: string): Promise<CleanerPayload> {
	const { python, cleaner, timeoutMs } = settings();
	return new Promise((resolvePromise, rejectPromise) => {
		const child = spawn(python, [cleaner, ...args], {
			stdio: ["pipe", "pipe", "pipe"],
			env: process.env,
		});
		let stdout = "";
		let stderr = "";
		let settled = false;

		const fail = (error: Error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			child.kill("SIGKILL");
			rejectPromise(error);
		};

		const timer = setTimeout(
			() => fail(new Error(`cleaner timed out after ${timeoutMs}ms`)),
			timeoutMs,
		);

		child.stdout.on("data", (chunk: Buffer) => {
			stdout += chunk.toString("utf8");
			if (stdout.length > 1024 * 1024)
				fail(new Error("cleaner output exceeded 1 MiB"));
		});
		child.stderr.on("data", (chunk: Buffer) => {
			stderr += chunk.toString("utf8");
		});
		child.on("error", fail);
		child.stdin.on("error", fail);
		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			if (code !== 0) {
				rejectPromise(new Error(stderr.trim() || `cleaner exited ${code}`));
				return;
			}
			try {
				resolvePromise(JSON.parse(stdout) as CleanerPayload);
			} catch (error) {
				rejectPromise(
					error instanceof Error ? error : new Error(String(error)),
				);
			}
		});
		child.stdin.end(stdin, "utf8");
	});
}

export default function promptHelper(pi: ExtensionAPI) {
	let enabled = envFlag("PI_PROMPT_HELPER_ENABLED", true);
	let state: State = { kind: "idle" };
	let loading: Promise<void> | undefined;
	const stats = { seen: 0, cleaned: 0, skipped: 0, errors: 0, charsSaved: 0 };

	const beginLoading = () => {
		if (loading || state.kind === "ready") return;
		state = { kind: "loading", startedAt: new Date().toISOString() };
		loading = runCleaner(["--health"], "")
			.then((payload) => {
				if (payload.ok !== true)
					throw new Error(payload.reason || "health check failed");
				state = {
					kind: "ready",
					mode: payload.mode || "regex",
					checkedAt: new Date().toISOString(),
				};
			})
			.catch((error) => {
				state = {
					kind: "failed",
					reason: error instanceof Error ? error.message : String(error),
					checkedAt: new Date().toISOString(),
				};
			})
			.finally(() => {
				loading = undefined;
			});
	};

	pi.registerCommand("exact", {
		description:
			"Send a prompt exactly as written, bypassing local prompt cleanup",
		handler(args, ctx) {
			const text = args.trim();
			if (!text) {
				notify(ctx, "Usage: /exact <prompt>", "warning");
				return;
			}
			pi.sendUserMessage(text);
		},
	});

	pi.registerCommand("prompt-cleaner-toggle", {
		description: "Toggle local prompt cleanup for this process",
		handler(_args, ctx) {
			enabled = !enabled;
			if (enabled) beginLoading();
			notify(ctx, `prompt cleaner ${enabled ? "enabled" : "disabled"}`);
		},
	});

	pi.registerCommand("prompt-cleaner-status", {
		description: "Show local prompt cleaner readiness",
		handler(_args, ctx) {
			if (enabled) beginLoading();
			const renderedState =
				state.kind === "ready"
					? `ready (${state.mode})`
					: state.kind === "failed"
						? `failed (${state.reason})`
						: state.kind;
			notify(
				ctx,
				`prompt cleaner ${enabled ? "enabled" : "disabled"}; state=${renderedState}`,
			);
		},
	});

	pi.registerCommand("prompt-cleaner-stats", {
		description: "Show prompt cleanup counters",
		handler(_args, ctx) {
			notify(
				ctx,
				`seen=${stats.seen} cleaned=${stats.cleaned} skipped=${stats.skipped} errors=${stats.errors} charsSaved=${stats.charsSaved}`,
			);
		},
	});

	pi.on("input", async (event) => {
		stats.seen += 1;
		if (shouldSkip(event, enabled)) {
			stats.skipped += 1;
			return { action: "continue" };
		}

		if (state.kind !== "ready") {
			beginLoading();
			stats.skipped += 1;
			return { action: "continue" };
		}

		try {
			const { minSavings } = settings();
			const payload = await runCleaner(
				["--min-savings", String(minSavings)],
				event.text,
			);
			if (
				payload.status === "cleaned" &&
				typeof payload.text === "string" &&
				payload.text !== event.text
			) {
				stats.cleaned += 1;
				stats.charsSaved += Math.max(
					0,
					event.text.length - payload.text.length,
				);
				return {
					action: "transform",
					text: payload.text,
					images: event.images,
				};
			}
			stats.skipped += 1;
			return { action: "continue" };
		} catch (error) {
			stats.errors += 1;
			state = {
				kind: "failed",
				reason: error instanceof Error ? error.message : String(error),
				checkedAt: new Date().toISOString(),
			};
			return { action: "continue" };
		}
	});
}
