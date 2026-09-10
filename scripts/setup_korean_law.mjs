#!/usr/bin/env node
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = join(root, "korean-law-mcp");

// --check: 빌드/키 상태만 출력하고 설치하지 않는다 (README 용량 안내용 진단).
if (process.argv.includes("--check")) {
	const built = existsSync(join(pkg, "build", "index.js"));
	const hasKey = Boolean(process.env.LAW_OC || process.env.KOREAN_LAW_API_KEY);
	process.stdout.write(
		`[lazyforensic] korean-law check: build=${built ? "ok" : "missing (≈680MB node_modules + build 필요)"} ` +
		`key=${hasKey ? "set" : "missing (LAW_OC 미설정)"}\n`,
	);
	if (!built) process.stdout.write("[hint] node scripts/setup_korean_law.mjs (최초 1회, 네트워크에 따라 수 분)\n");
	if (!hasKey) process.stdout.write("[hint] cp .env.example .env 후 LAW_OC 기입\n");
	process.exit(built && hasKey ? 0 : 78);
}

if (!existsSync(join(pkg, "package.json"))) {
	process.stderr.write("[lazyforensic] korean-law-mcp/package.json is missing.\n");
	process.exit(1);
}

function run(command, args) {
	const result = spawnSync(command, args, {
		cwd: pkg,
		stdio: "inherit",
		shell: process.platform === "win32",
		windowsHide: true,
	});
	if (result.error) {
		process.stderr.write(`[lazyforensic] ${command} failed: ${result.error.message}\n`);
		process.exit(1);
	}
	if (result.status !== 0) {
		process.stderr.write(
			`[lazyforensic] ${command} ${args.join(" ")} exited ${result.status ?? 1}\n`,
		);
		process.exit(result.status ?? 1);
	}
}

run("npm", ["install", "--ignore-scripts"]);
run("npm", ["run", "build"]);

if (!existsSync(join(pkg, "build", "index.js"))) {
	process.stderr.write("[lazyforensic] build finished but korean-law-mcp/build/index.js is missing.\n");
	process.exit(1);
}

process.stdout.write(
	"[lazyforensic] korean-law-mcp build ready. Set LAW_OC or KOREAN_LAW_API_KEY before starting MCP. Do not fly deploy from this repo.\n",
);
