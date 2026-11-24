#!/usr/bin/env node
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = join(root, "korean-law-mcp");
// Pinned to a commit so installs are reproducible — bump deliberately.
// Override with KOREAN_LAW_MCP_REF=<40-hex commit> (a tag or branch only works for a fresh clone).
const PINNED_REF = "44ff16ec884395a3e7424ca8ff76caa081c9390c";
const REF = process.env.KOREAN_LAW_MCP_REF || PINNED_REF;
const LOCKFILE = "package-lock.json";
// Build input record: which commit and lockfile produced build/index.js.
const RECORD = join(pkg, ".lazyforensic-build.json");
const shell = process.platform === "win32";

function getLogFileArg(argv) {
	for (let i = 0; i < argv.length; i++) {
		if (argv[i] === "--log-file" && argv[i + 1] && !argv[i + 1].startsWith("--")) return argv[i + 1];
		if (argv[i].startsWith("--log-file=")) {
			const v = argv[i].slice("--log-file=".length);
			if (v) return v;
		}
	}
	return null;
}

function readRecord() {
	try {
		return JSON.parse(readFileSync(RECORD, "utf8"));
	} catch {
		return null;
	}
}

// --check: 빌드/키 상태만 출력하고 설치하지 않는다 (README 용량 안내용 진단).
if (process.argv.includes("--check")) {
	const built = existsSync(join(pkg, "build", "index.js"));
	const hasKey = Boolean(process.env.LAW_OC || process.env.KOREAN_LAW_API_KEY);
	process.stdout.write(
		`[lazyforensic] korean-law check: build=${built ? "ok" : "missing (≈680MB node_modules + build 필요)"} ` +
		`key=${hasKey ? "set" : "missing (LAW_OC 미설정)"}\n`,
	);
	const record = built ? readRecord() : null;
	if (built) {
		process.stdout.write(
			record
				? `[lazyforensic] build input: commit=${record.commit} ${LOCKFILE} sha256=${record.package_lock_sha256}\n`
				: "[lazyforensic] build input: 기록 없음 (이 스크립트로 빌드하지 않았거나 이전 버전에서 빌드)\n",
		);
	}
	if (!built) process.stdout.write("[hint] node scripts/setup_korean_law.mjs (최초 1회, 네트워크에 따라 수 분)\n");
	if (!hasKey) process.stdout.write("[hint] cp .env.example .env 후 LAW_OC 기입\n");
	// A3-1: ToS 판정 1줄 기록 — --log-file 지정 시 파일에 append, 없으면 stderr만. 기존 stdout/exit 불변.
	try {
		const tosAck = process.env.LAW_TOS_ACK === "1";
		const tosLine = `[${new Date().toISOString()}] LAW_TOS_ACK=${tosAck ? "1 (ack, bypass=enabled)" : "0 (missing, bypass=disabled)"} terms=2026.09\n`;
		const logFile = getLogFileArg(process.argv.slice(2));
		if (logFile) {
			try {
				const dir = dirname(logFile);
				if (dir && dir !== "." && dir !== "") mkdirSync(dir, { recursive: true });
			} catch {}
			appendFileSync(logFile, tosLine, "utf8");
		} else {
			process.stderr.write(tosLine);
		}
	} catch {}
	process.exit(built && hasKey ? 0 : 78);
}

function fail(message) {
	process.stderr.write(`[lazyforensic] ${message}\n`);
	process.exit(1);
}

function git(args) {
	const res = spawnSync("git", args, { cwd: pkg, encoding: "utf8", shell, windowsHide: true });
	return { ok: !res.error && res.status === 0, out: (res.stdout || "").trim() };
}

function sha256(path) {
	return createHash("sha256").update(readFileSync(path)).digest("hex");
}

let expected = /^[0-9a-f]{40}$/i.test(REF) ? REF.toLowerCase() : null;

if (!existsSync(join(pkg, "package.json"))) {
	// korean-law-mcp lives in its own repo; clone it into place on first setup.
	process.stdout.write(`[lazyforensic] cloning korean-law-mcp @ ${REF} …\n`);
	mkdirSync(pkg, { recursive: true });
	const steps = [
		["init"],
		["remote", "add", "origin", "https://github.com/daeryundf2-prog/korean-law-mcp.git"],
		["fetch", "--depth", "1", "origin", REF],
		["checkout", "--detach", "FETCH_HEAD"],
	];
	for (const args of steps) {
		const res = spawnSync("git", args, { cwd: pkg, stdio: "inherit", shell, windowsHide: true });
		if (res.error || res.status !== 0) {
			fail("korean-law-mcp clone failed — clone https://github.com/daeryundf2-prog/korean-law-mcp into ./korean-law-mcp manually.");
		}
	}
	if (!expected) expected = git(["rev-parse", "FETCH_HEAD"]).out.toLowerCase();
}

// An existing checkout is reused only when it is exactly the pinned commit with an unmodified lockfile.
if (!existsSync(join(pkg, ".git"))) {
	fail("korean-law-mcp에 git 기록이 없어 고정 커밋을 확인할 수 없습니다. korean-law-mcp 폴더를 지우고 다시 실행하세요.");
}
if (!expected) {
	fail(`KOREAN_LAW_MCP_REF=${REF}는 기존 체크아웃에서 커밋으로 확인할 수 없습니다. 40자리 커밋을 지정하세요.`);
}
const head = git(["rev-parse", "HEAD"]);
if (!head.ok) fail("korean-law-mcp의 현재 커밋을 읽을 수 없습니다.");
const commit = head.out.toLowerCase();
if (commit !== expected) {
	fail(
		`기존 korean-law-mcp 체크아웃이 고정 커밋과 다릅니다 — 재사용하지 않습니다.\n` +
		`  현재 커밋: ${commit}\n  고정 커밋: ${expected}\n` +
		`  고정 커밋으로 맞추려면 korean-law-mcp 폴더를 지우고 다시 실행하세요. ` +
		`현재 커밋을 의도적으로 쓰려면 KOREAN_LAW_MCP_REF=${commit} 로 명시하세요.`,
	);
}
const status = git(["status", "--porcelain", "--untracked-files=no"]);
if (!status.ok) fail("korean-law-mcp 작업 트리 상태를 읽을 수 없습니다.");
if (status.out) {
	fail(`korean-law-mcp의 추적 파일이 커밋 ${commit}과 다릅니다 — 재사용하지 않습니다.\n${status.out}`);
}
if (!git(["ls-files", "--error-unmatch", LOCKFILE]).ok) {
	fail(`커밋 ${commit}에 ${LOCKFILE}가 없어 같은 의존성으로 재현 설치할 수 없습니다.`);
}
const lockPath = join(pkg, LOCKFILE);
const lockSha = sha256(lockPath);

function run(command, args) {
	const result = spawnSync(command, args, { cwd: pkg, stdio: "inherit", shell, windowsHide: true });
	if (result.error) fail(`${command} failed: ${result.error.message}`);
	if (result.status !== 0) {
		process.stderr.write(`[lazyforensic] ${command} ${args.join(" ")} exited ${result.status ?? 1}\n`);
		process.exit(result.status ?? 1);
	}
}

// npm ci installs exactly the committed lockfile and fails instead of rewriting it.
run("npm", ["ci", "--ignore-scripts"]);
run("npm", ["run", "build"]);

if (!existsSync(join(pkg, "build", "index.js"))) {
	fail("build finished but korean-law-mcp/build/index.js is missing.");
}
if (sha256(lockPath) !== lockSha) fail(`${LOCKFILE}가 설치 중에 바뀌었습니다.`);

const npmVersion = spawnSync("npm", ["--version"], { cwd: pkg, encoding: "utf8", shell, windowsHide: true });
const manifest = JSON.parse(readFileSync(join(pkg, "package.json"), "utf8"));
writeFileSync(RECORD, JSON.stringify({
	ref: REF,
	commit,
	package_version: manifest.version ?? null,
	package_lock_sha256: lockSha,
	node: process.version,
	npm: (npmVersion.stdout || "").trim() || null,
	built_at: new Date().toISOString(),
}, null, 2) + "\n");

process.stdout.write(
	`[lazyforensic] korean-law-mcp build ready (commit ${commit}, ${LOCKFILE} sha256 ${lockSha}; ${RECORD}).\n` +
	"[lazyforensic] Set LAW_OC or KOREAN_LAW_API_KEY before starting MCP. Do not fly deploy from this repo.\n",
);
