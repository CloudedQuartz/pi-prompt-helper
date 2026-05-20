#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const required = [
	"package.json",
	"src/index.ts",
	"python/prompt_cleaner.py",
	"tests/test_prompt_cleaner.py",
	"tests/extension.test.mjs",
	"README.md",
];

function fail(message) {
	console.error(`smoke-extension: ${message}`);
	process.exit(1);
}

for (const file of required) {
	if (!existsSync(resolve(root, file))) fail(`missing ${file}`);
}

const pkg = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
if (pkg.name !== "pi-prompt-helper")
	fail("package name must be pi-prompt-helper");
if (!pkg.pi?.extensions?.includes("./src/index.ts"))
	fail("package.json must register ./src/index.ts");

const removedName = ["squ", "aizer"].join("");
for (const file of required) {
	const text = readFileSync(resolve(root, file), "utf8").toLowerCase();
	if (text.includes(removedName)) fail(`${file} contains removed naming`);
}

const python =
	process.env.PI_PROMPT_HELPER_PYTHON || process.env.PYTHON || "python3";
const health = spawnSync(
	python,
	[resolve(root, "python/prompt_cleaner.py"), "--health"],
	{ encoding: "utf8" },
);
if (health.status !== 0)
	fail(`health failed: ${health.stderr || health.stdout}`);
if (JSON.parse(health.stdout).ok !== true) fail("health did not return ok");

const clean = spawnSync(
	python,
	[resolve(root, "python/prompt_cleaner.py"), "--min-savings", "0"],
	{
		input: "Please, um, help with this.",
		encoding: "utf8",
	},
);
if (clean.status !== 0) fail(`clean failed: ${clean.stderr || clean.stdout}`);
if (JSON.parse(clean.stdout).status !== "cleaned")
	fail("cleaner did not clean sample prompt");

console.log("smoke-extension: ok");
