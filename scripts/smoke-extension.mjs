#!/usr/bin/env node
import { readFileSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const required = [
  "package.json",
  "src/index.ts",
  "python/squaizer_sidecar.py",
  "python/resources/disfluencies.json",
  "python/resources/hedge_uncertainty.json",
  "python/resources/discourse_markers.json",
  "python/resources/politeness_request_framing.json",
  "python/resources/intensifiers.json",
  "tests/test_squaizer_sidecar.py",
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
if (pkg.name !== "pi-prompt-helper") fail("package name must be pi-prompt-helper");
if (pkg.type !== "module") fail("package must use type=module");
if (!pkg.pi || !Array.isArray(pkg.pi.extensions) || !pkg.pi.extensions.includes("./src/index.ts")) {
  fail("package.json must include pi.extensions ['./src/index.ts']");
}

const source = readFileSync(resolve(root, "src/index.ts"), "utf8");
for (const needle of ["pi.on(\"input\"", "action: \"transform\"", "spawn(", "PI_PROMPT_HELPER_ENABLED"]) {
  if (!source.includes(needle)) fail(`src/index.ts missing ${needle}`);
}

for (const resource of required.filter((file) => file.startsWith("python/resources/"))) {
  const parsed = JSON.parse(readFileSync(resolve(root, resource), "utf8"));
  if (!parsed.metadata || !parsed.categories) fail(`${resource} missing metadata/categories`);
}

const python = process.env.PI_PROMPT_HELPER_PYTHON || process.env.PYTHON || "python3";
const pyCompile = spawnSync(python, ["-m", "py_compile", resolve(root, "python/squaizer_sidecar.py")], {
  encoding: "utf8",
});
if (pyCompile.status !== 0) fail(`py_compile failed: ${pyCompile.stderr || pyCompile.stdout}`);

const sidecar = spawnSync(
  python,
  [resolve(root, "python/squaizer_sidecar.py"), "--mode", "regex", "--min-savings", "0"],
  { input: "Please, um, help with this.", encoding: "utf8" },
);
if (sidecar.status !== 0) fail(`sidecar failed: ${sidecar.stderr || sidecar.stdout}`);
const payload = JSON.parse(sidecar.stdout);
if (payload.status !== "compressed" || !payload.text.includes("help")) fail("sidecar did not return expected compressed JSON");

console.log("smoke-extension: ok");
