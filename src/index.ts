import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type SidecarResult = {
	status?: string;
	text?: string;
	original_chars?: number;
	compressed_chars?: number;
	savings_ratio?: number;
	reason?: string;
	mode?: string;
	deleted?: Array<{ text?: string; category?: string }>;
};

type Stats = {
	seen: number;
	compressed: number;
	skipped: number;
	errors: number;
	charsSaved: number;
	last?: SidecarResult & { at: string };
};

const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_SIDECAR = resolve(EXTENSION_DIR, "../python/squaizer_sidecar.py");

function envFlag(name: string, defaultValue: boolean): boolean {
	const raw = process.env[name];
	if (raw === undefined || raw === "") return defaultValue;
	return !["0", "false", "no", "off", "disabled"].includes(raw.toLowerCase());
}

function envNumber(name: string, defaultValue: number): number {
	const raw = process.env[name];
	if (!raw) return defaultValue;
	const parsed = Number(raw);
	return Number.isFinite(parsed) ? parsed : defaultValue;
}

type NotifyLevel = "info" | "warning" | "error";

function notify(
	ctx: {
		hasUI?: boolean;
		ui?: { notify?: (message: string, level?: NotifyLevel) => void };
	},
	message: string,
	level: NotifyLevel = "info",
) {
	if (ctx.hasUI && ctx.ui?.notify) {
		ctx.ui.notify(message, level);
	}
}

function shouldSkipInput(
	event: { text: string; images?: unknown[]; source?: string },
	enabled: boolean,
): string | undefined {
	if (!enabled) return "disabled";
	if (event.source === "extension") return "extension-source";
	if (!event.text.trim()) return "blank";
	if (
		!envFlag("PI_PROMPT_HELPER_COMPRESS_SLASH", false) &&
		event.text.trimStart().startsWith("/")
	) {
		return "slash-command";
	}
	if (
		!envFlag("PI_PROMPT_HELPER_COMPRESS_IMAGES", false) &&
		event.images &&
		event.images.length > 0
	) {
		return "attached-images";
	}
	return undefined;
}

function runSidecar(
	text: string,
	minSavings: number,
	mode: string,
	timeoutMs: number,
): Promise<SidecarResult> {
	const python =
		process.env.PI_PROMPT_HELPER_PYTHON || process.env.PYTHON || "python3";
	const sidecar = process.env.PI_PROMPT_HELPER_SIDECAR || DEFAULT_SIDECAR;
	const args = [sidecar, "--min-savings", String(minSavings), "--mode", mode];

	return new Promise((resolvePromise, rejectPromise) => {
		const child = spawn(python, args, {
			stdio: ["pipe", "pipe", "pipe"],
			env: process.env,
		});

		let stdout = "";
		let stderr = "";
		let settled = false;
		const maxOutput = 1024 * 1024;
		const timer = setTimeout(() => {
			if (settled) return;
			settled = true;
			child.kill("SIGKILL");
			rejectPromise(new Error(`sidecar timed out after ${timeoutMs}ms`));
		}, timeoutMs);

		child.stdout.on("data", (chunk: Buffer) => {
			stdout += chunk.toString("utf8");
			if (stdout.length > maxOutput && !settled) {
				settled = true;
				child.kill("SIGKILL");
				rejectPromise(new Error("sidecar output exceeded 1 MiB"));
			}
		});
		child.stderr.on("data", (chunk: Buffer) => {
			stderr += chunk.toString("utf8");
		});
		child.stdin.on("error", (error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			child.kill("SIGKILL");
			rejectPromise(error);
		});
		child.on("error", (error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			rejectPromise(error);
		});
		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			if (code !== 0) {
				rejectPromise(new Error(`sidecar exited ${code}: ${stderr.trim()}`));
				return;
			}
			try {
				resolvePromise(JSON.parse(stdout) as SidecarResult);
			} catch (error) {
				rejectPromise(
					new Error(
						`invalid sidecar JSON: ${error instanceof Error ? error.message : String(error)}`,
					),
				);
			}
		});

		try {
			child.stdin.end(text, "utf8");
		} catch (error) {
			if (!settled) {
				settled = true;
				clearTimeout(timer);
				child.kill("SIGKILL");
				rejectPromise(
					error instanceof Error ? error : new Error(String(error)),
				);
			}
		}
	});
}

export default function promptHelperExtension(pi: ExtensionAPI) {
	let enabled = envFlag("PI_PROMPT_HELPER_ENABLED", true);
	const stats: Stats = {
		seen: 0,
		compressed: 0,
		skipped: 0,
		errors: 0,
		charsSaved: 0,
	};

	pi.registerCommand("exact", {
		description:
			"Send a prompt exactly as written, bypassing prompt compression",
		handler: async (args, ctx) => {
			const prompt = args.trim();
			if (!prompt) {
				notify(ctx, "Usage: /exact <prompt>", "warning");
				return;
			}
			pi.sendUserMessage(prompt);
		},
	});

	pi.registerCommand("prompt-helper-toggle", {
		description: "Toggle local prompt compression for this pi process",
		handler: async (_args, ctx) => {
			enabled = !enabled;
			notify(
				ctx,
				`pi-prompt-helper ${enabled ? "enabled" : "disabled"}`,
				"info",
			);
		},
	});

	pi.registerCommand("prompt-helper-status", {
		description: "Show pi-prompt-helper configuration and last sidecar result",
		handler: async (_args, ctx) => {
			const minSavings = envNumber("PI_PROMPT_HELPER_MIN_SAVINGS", 0.03);
			const timeoutMs = envNumber("PI_PROMPT_HELPER_TIMEOUT_MS", 2000);
			const mode = process.env.PI_PROMPT_HELPER_MODE || "auto";
			const last = stats.last
				? `\nLast: ${stats.last.status} (${stats.last.reason || "ok"}, saved ${stats.last.original_chars && stats.last.compressed_chars ? stats.last.original_chars - stats.last.compressed_chars : 0} chars)`
				: "\nLast: none";
			notify(
				ctx,
				`pi-prompt-helper ${enabled ? "enabled" : "disabled"}\nMode: ${mode}\nMin savings: ${minSavings}\nTimeout: ${timeoutMs}ms\nSidecar: ${process.env.PI_PROMPT_HELPER_SIDECAR || DEFAULT_SIDECAR}${last}`,
				"info",
			);
		},
	});

	pi.registerCommand("prompt-helper-stats", {
		description: "Show prompt compression counters for this pi process",
		handler: async (_args, ctx) => {
			notify(
				ctx,
				`pi-prompt-helper stats: seen=${stats.seen}, compressed=${stats.compressed}, skipped=${stats.skipped}, errors=${stats.errors}, charsSaved=${stats.charsSaved}`,
				"info",
			);
		},
	});

	pi.on("input", async (event, _ctx) => {
		stats.seen += 1;
		const skipReason = shouldSkipInput(event, enabled);
		if (skipReason) {
			stats.skipped += 1;
			return { action: "continue" };
		}

		try {
			const minSavings = envNumber("PI_PROMPT_HELPER_MIN_SAVINGS", 0.03);
			const timeoutMs = envNumber("PI_PROMPT_HELPER_TIMEOUT_MS", 2000);
			const mode = process.env.PI_PROMPT_HELPER_MODE || "auto";
			const result = await runSidecar(event.text, minSavings, mode, timeoutMs);
			stats.last = { ...result, at: new Date().toISOString() };

			if (
				result.status === "compressed" &&
				typeof result.text === "string" &&
				result.text &&
				result.text !== event.text
			) {
				stats.compressed += 1;
				stats.charsSaved += Math.max(0, event.text.length - result.text.length);
				return { action: "transform", text: result.text, images: event.images };
			}
			stats.skipped += 1;
			return { action: "continue" };
		} catch (error) {
			stats.errors += 1;
			stats.last = {
				status: "error",
				text: event.text,
				reason: error instanceof Error ? error.message : String(error),
				at: new Date().toISOString(),
			};
			return { action: "continue" };
		}
	});
}
