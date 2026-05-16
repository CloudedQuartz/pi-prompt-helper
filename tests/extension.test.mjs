import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import promptHelperExtension from "../.tmp-test/src/index.js";

function withEnv(patch, fn) {
	const old = new Map(Object.keys(patch).map((key) => [key, process.env[key]]));
	for (const [key, value] of Object.entries(patch)) {
		if (value === undefined) delete process.env[key];
		else process.env[key] = String(value);
	}
	return Promise.resolve()
		.then(fn)
		.finally(() => {
			for (const [key, value] of old) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
		});
}

function makePi() {
	const commands = new Map();
	const handlers = new Map();
	const sentUserMessages = [];
	return {
		commands,
		handlers,
		sentUserMessages,
		pi: {
			registerCommand(name, config) {
				commands.set(name, config);
			},
			on(event, handler) {
				handlers.set(event, handler);
			},
			sendUserMessage(content, options) {
				sentUserMessages.push({ content, options });
			},
		},
	};
}

test("extension transforms only compressed sidecar results", async () => {
	const dir = await mkdtemp(join(tmpdir(), "pi-prompt-helper-test-"));
	const sidecar = join(dir, "sidecar.mjs");
	await writeFile(
		sidecar,
		"#!/usr/bin/env node\nprocess.stdin.resume();process.stdin.on('end',()=>{console.log(JSON.stringify({status:'compressed',text:'compressed prompt'}));});\n",
		{ mode: 0o755 },
	);

	try {
		await withEnv(
			{
				PI_PROMPT_HELPER_PYTHON: process.execPath,
				PI_PROMPT_HELPER_SIDECAR: sidecar,
				PI_PROMPT_HELPER_MIN_SAVINGS: "0",
			},
			async () => {
				const harness = makePi();
				promptHelperExtension(harness.pi);
				const result = await harness.handlers.get("input")(
					{ text: "original prompt", source: "interactive" },
					{},
				);
				assert.deepEqual(result, {
					action: "transform",
					text: "compressed prompt",
					images: undefined,
				});
			},
		);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});

test("extension skips slash commands by default", async () => {
	const harness = makePi();
	promptHelperExtension(harness.pi);
	const result = await harness.handlers.get("input")(
		{ text: "/model", source: "interactive" },
		{},
	);
	assert.deepEqual(result, { action: "continue" });
});

test("/exact sends an extension-originated bypass prompt", async () => {
	const harness = makePi();
	promptHelperExtension(harness.pi);
	await harness.commands.get("exact").handler("  Please keep this exact.  ", {
		hasUI: false,
		ui: { notify() {} },
	});
	assert.deepEqual(harness.sentUserMessages, [
		{ content: "Please keep this exact.", options: undefined },
	]);
});

test("/exact without prompt reports usage and does not send", async () => {
	const harness = makePi();
	const notifications = [];
	promptHelperExtension(harness.pi);
	await harness.commands.get("exact").handler("   ", {
		hasUI: true,
		ui: {
			notify(message, level) {
				notifications.push({ message, level });
			},
		},
	});
	assert.deepEqual(harness.sentUserMessages, []);
	assert.deepEqual(notifications, [
		{ message: "Usage: /exact <prompt>", level: "warning" },
	]);
});

test("extension fails open on sidecar stdin errors", async () => {
	const dir = await mkdtemp(join(tmpdir(), "pi-prompt-helper-test-"));
	const sidecar = join(dir, "exit.mjs");
	await writeFile(sidecar, "#!/usr/bin/env node\nprocess.exit(0);\n", {
		mode: 0o755,
	});

	try {
		await withEnv(
			{
				PI_PROMPT_HELPER_PYTHON: process.execPath,
				PI_PROMPT_HELPER_SIDECAR: sidecar,
				PI_PROMPT_HELPER_TIMEOUT_MS: "1000",
			},
			async () => {
				const harness = makePi();
				promptHelperExtension(harness.pi);
				const result = await harness.handlers.get("input")(
					{ text: "x".repeat(2_000_000), source: "interactive" },
					{},
				);
				assert.deepEqual(result, { action: "continue" });
			},
		);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});
