#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { stdin } from "node:process";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
function readStdin(limitMs) {
	return new Promise((resolve) => {
		if (stdin.isTTY) {
			resolve("");
			return;
		}
		const chunks = [];
		const finish = () => {
			clearTimeout(timer);
			stdin.removeAllListeners();
			resolve(Buffer.concat(chunks).toString("utf8"));
		};
		const timer = setTimeout(finish, limitMs);
		stdin.on("data", (chunk) => {
			chunks.push(chunk);
		});
		stdin.on("end", finish);
		stdin.on("error", () => {
			clearTimeout(timer);
			resolve("");
		});
	});
}

function hookEventName(raw) {
	try {
		const parsed = JSON.parse(raw.trim());
		if (parsed && typeof parsed.hook_event_name === "string" && parsed.hook_event_name) {
			return parsed.hook_event_name;
		}
	} catch {
		// fail open
	}
	return "SessionStart";
}

function koreanLawStatus() {
	const built = existsSync(join(root, "korean-law-mcp", "build", "index.js"));
	const hasKey = Boolean(process.env.LAW_OC || process.env.KOREAN_LAW_API_KEY);
	if (!built) {
		return "missing-build";
	}
	if (!hasKey) {
		return "missing-LAW_OC";
	}
	return "ready";
}

function additionalContext() {
	try {
		const gemini = readFileSync(join(root, "GEMINI.md"), "utf8").replace(/\r\n/g, "\n").trim();
		return `${gemini}\n\n## 런타임\n\nkorean_law: ${koreanLawStatus()}`;
	} catch {
		return "";
	}
}
// ---------------------------------------------------------------------------
// Self-update (SessionStart): pull from origin/main when safe to do so.
// ---------------------------------------------------------------------------

/** Run git in dir; return stdout string on success, null on any error/timeout. */
function git(dir, args, timeoutMs = 2000) {
	const r = spawnSync("git", ["-C", dir, ...args], { encoding: "utf8", timeout: timeoutMs });
	if (r.error || r.signal != null || r.status !== 0) return null;
	return r.stdout;
}

/**
 * Attempt a safe fast-forward self-update of the plugin from origin/main.
 * Guards: must be on branch main with an origin remote; throttled to once per 6 h;
 * respects LAZYFORENSIC_NO_AUTO_UPDATE=1; never stashes, resets, or touches dirty trees.
 * Writes one line to errOut (default process.stderr) on update or when behind+blocked.
 * Never throws; never causes a non-zero exit.
 *
 * @param {string}   pluginRoot  Absolute path to the plugin checkout (default: root).
 * @param {object}   env         Process environment (default: process.env).
 * @param {object}   errOut      Writable stream for notices (default: process.stderr).
 */
export function autoUpdate(pluginRoot = root, env = process.env, errOut = process.stderr) {
	try {
		// Opt-out env var.
		if (env.LAZYFORENSIC_NO_AUTO_UPDATE === "1") return;

		// Must be a git checkout on branch main.
		const branch = git(pluginRoot, ["symbolic-ref", "--short", "HEAD"]);
		if (!branch || branch.trim() !== "main") return;

		// Must have an origin remote.
		const remotes = git(pluginRoot, ["remote"]);
		if (!remotes || !remotes.split("\n").map((s) => s.trim()).includes("origin")) return;

		// Throttle: at most once every 6 hours. The stamp lives inside .git so it never dirties the tree.
		const gitDirOut = git(pluginRoot, ["rev-parse", "--absolute-git-dir"]);
		if (!gitDirOut) return;
		const stateDir = gitDirOut.trim();
		const stateFile = join(stateDir, "lazyforensic-last-update-check");
		try {
			if (existsSync(stateFile)) {
				const ts = parseInt(readFileSync(stateFile, "utf8").trim(), 10);
				if (!isNaN(ts) && Date.now() - ts < SIX_HOURS_MS) return;
			}
		} catch { /* ignore */ }

		// Record the check time before the fetch (prevents hammering on network errors).
		try {
			mkdirSync(stateDir, { recursive: true });
			writeFileSync(stateFile, String(Date.now()), "utf8");
		} catch { /* ignore */ }

		// Snapshot HEAD before fetch.
		const headOut = git(pluginRoot, ["rev-parse", "HEAD"]);
		if (!headOut) return;
		const beforeSha = headOut.trim();

		// Fetch with hard 3 s timeout; any failure stays silent.
		const fetchResult = spawnSync("git", ["-C", pluginRoot, "fetch", "origin", "main"], {
			encoding: "utf8",
			timeout: 3000,
		});
		if (fetchResult.error || fetchResult.signal != null || fetchResult.status !== 0) return;

		// What did origin/main land on?
		const fetchOut = git(pluginRoot, ["rev-parse", "FETCH_HEAD"]);
		if (!fetchOut) return;
		const fetchSha = fetchOut.trim();

		// Count commits we're behind; none (up to date or only local commits ahead) stays silent.
		const countOut = git(pluginRoot, ["rev-list", "--count", `${beforeSha}..${fetchSha}`]);
		if (countOut === null) return;
		const n = countOut.trim();
		if (n === "0") return;

		// Check working-tree cleanliness (porcelain is empty iff no changes and no untracked).
		const statusOut = git(pluginRoot, ["status", "--porcelain"]);
		if (statusOut === null) return;
		const dirty = statusOut.trim().length > 0;

		if (!dirty) {
			// Try fast-forward merge.
			const merge = spawnSync("git", ["-C", pluginRoot, "merge", "--ff-only", fetchSha], {
				encoding: "utf8",
				timeout: 2000,
			});
			if (merge.status === 0 && merge.signal == null && !merge.error) {
				const old7 = beforeSha.slice(0, 7);
				const new7 = fetchSha.slice(0, 7);
				errOut.write(`LazyForensic updated ${old7}..${new7} (${n} commits)\n`);
			} else {
				// History diverged (local commits) — cannot fast-forward.
				errOut.write(
					`LazyForensic: ${n} updates on origin/main; local commits differ, not auto-updated. Run: git -C ${pluginRoot} pull --rebase origin main\n`,
				);
			}
		} else {
			// Dirty working tree — leave everything alone.
			errOut.write(
				`LazyForensic: ${n} updates on origin/main; local changes present, not auto-updated. Run: git -C ${pluginRoot} stash && git -C ${pluginRoot} pull --ff-only\n`,
			);
		}
	} catch { /* never propagate */ }
}

// Compare real paths: the hook runs this file as "${PLUGIN_ROOT}/scripts/session_start.mjs", and PLUGIN_ROOT may be a symlink.
const realOrSelf = (p) => {
	try {
		return realpathSync(p);
	} catch {
		return p;
	}
};
const isMain = Boolean(process.argv[1]) && realOrSelf(fileURLToPath(import.meta.url)) === realOrSelf(process.argv[1]);
if (isMain) {
	const raw = await readStdin(250);
	autoUpdate(root);
	const context = additionalContext();
	if (context) {
		process.stdout.write(
			`${JSON.stringify({
				hookSpecificOutput: {
					hookEventName: hookEventName(raw),
					additionalContext: context,
				},
			})}\n`,
		);
	}
	process.exit(0);
}
