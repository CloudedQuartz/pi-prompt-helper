import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import promptHelper from "../.tmp-test/src/index.js";

async function withEnv(patch, fn) {
	const previous = new Map(
		Object.keys(patch).map((key) => [key, process.env[key]]),
	);
	for (const [key, value] of Object.entries(patch)) {
		if (value === undefined) delete process.env[key];
		else process.env[key] = String(value);
	}
	try {
		return await fn();
	} finally {
		for (const [key, value] of previous) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
	}
}

function harness() {
	const commands = new Map();
	const handlers = new Map();
	const sent = [];
	return {
		commands,
		handlers,
		sent,
		pi: {
			on(name, handler) {
				handlers.set(name, handler);
			},
			registerCommand(name, command) {
				commands.set(name, command);
			},
			sendUserMessage(text) {
				sent.push(text);
			},
		},
	};
}

async function wait(ms) {
	await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForResult(fn, predicate, timeoutMs = 1000) {
	const deadline = Date.now() + timeoutMs;
	let result;
	while (Date.now() < deadline) {
		result = await fn();
		if (predicate(result)) return result;
		await wait(10);
	}
	return result;
}

test("first eligible input starts lazy loading and continues", async () => {
	const dir = await mkdtemp(join(tmpdir(), "prompt-helper-"));
	const cleaner = join(dir, "cleaner.mjs");
	await writeFile(
		cleaner,
		`setTimeout(() => console.log(JSON.stringify({ok:true,mode:'regex'})), 60);`,
	);
	try {
		await withEnv(
			{
				PI_PROMPT_HELPER_PYTHON: process.execPath,
				PI_PROMPT_HELPER_CLEANER: cleaner,
			},
			async () => {
				const app = harness();
				promptHelper(app.pi);
				const result = await app.handlers.get("input")({
					text: "Please help",
					source: "user",
				});
				assert.deepEqual(result, { action: "continue" });
			},
		);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});

test("ready cleaner can transform later input", async () => {
	const dir = await mkdtemp(join(tmpdir(), "prompt-helper-"));
	const cleaner = join(dir, "cleaner.mjs");
	await writeFile(
		cleaner,
		`if (process.argv.includes('--health')) console.log(JSON.stringify({ok:true,mode:'regex'}));
else { process.stdin.resume(); process.stdin.on('end', () => console.log(JSON.stringify({status:'cleaned',text:'clean text'}))); }`,
	);
	try {
		await withEnv(
			{
				PI_PROMPT_HELPER_PYTHON: process.execPath,
				PI_PROMPT_HELPER_CLEANER: cleaner,
			},
			async () => {
				const app = harness();
				promptHelper(app.pi);
				const input = app.handlers.get("input");
				assert.deepEqual(await input({ text: "first", source: "user" }), {
					action: "continue",
				});
				const result = await waitForResult(
					() => input({ text: "second", source: "user", images: [] }),
					(value) => value.action === "transform",
				);
				assert.deepEqual(result, {
					action: "transform",
					text: "clean text",
					images: [],
				});
			},
		);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});

test("cleaner failure fails open", async () => {
	const dir = await mkdtemp(join(tmpdir(), "prompt-helper-"));
	const cleaner = join(dir, "cleaner.mjs");
	await writeFile(
		cleaner,
		`if (process.argv.includes('--health')) console.log(JSON.stringify({ok:true,mode:'regex'})); else process.exit(2);`,
	);
	try {
		await withEnv(
			{
				PI_PROMPT_HELPER_PYTHON: process.execPath,
				PI_PROMPT_HELPER_CLEANER: cleaner,
			},
			async () => {
				const app = harness();
				promptHelper(app.pi);
				const input = app.handlers.get("input");
				await input({ text: "first", source: "user" });
				await wait(25);
				assert.deepEqual(await input({ text: "second", source: "user" }), {
					action: "continue",
				});
			},
		);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});

test("slash commands are skipped by default", async () => {
	const app = harness();
	promptHelper(app.pi);
	assert.deepEqual(
		await app.handlers.get("input")({ text: "/help", source: "user" }),
		{ action: "continue" },
	);
});

test("exact command sends raw prompt", async () => {
	const app = harness();
	promptHelper(app.pi);
	await app.commands.get("exact").handler("  keep this exact  ", {});
	assert.deepEqual(app.sent, ["keep this exact"]);
});
