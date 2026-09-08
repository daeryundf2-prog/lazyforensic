#!/usr/bin/env node
/**
 * hallucination_guard.mjs — PostToolUse 호스트 강제 할루시네이션 가드
 * "보고서" / "검토해줘" 트리거가 포함된 모든 쓰기 후 무조건 실행을 목표로 한다.
 *
 * 정직한 동작 규약 (docs/GAPS.md 참고):
 * - 이 훅은 PostToolUse다. 파일이 이미 디스크에 쓰인 뒤 검증하므로 차단은 "사후 게이트"다 —
 *   호스트가 exit 1 을 받아 다음 턴에서 수정을 강제하는 방식이다. 이미 쓰인 파일을 지우지 않는다.
 * - 실제 차단은 호스트가 hooks.json 의 failurePolicy: FAIL_CLOSED 로 exit 1 을 해석할 때만 작동한다.
 * - Python 인터프리터가 전무하면 검증을 실행할 수 없어 경고 후 통과(exit 0)한다. FAIL_OPEN.
 * - 스킵 경로: .md/.html/.txt 외 확장자, 호스트 미훅 경로, 키워드 없는 비보고서 쓰기.
 *
 * stdin 규약: 1.5초 데드라인 비동기 읽기 (Linux/Windows 파이프 호환 — fstat.size 미사용).
 */
import fs from 'fs';
import path from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { stdin } from 'node:process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

// ---------------------------------------------------------------------------
// 입력 수집 (evidence_guard.mjs 와 동일 규약 — 소스별 독립 파싱)
// ---------------------------------------------------------------------------

function readStdin(limitMs) {
	return new Promise((resolve) => {
		if (stdin.isTTY) {
			resolve('');
			return;
		}
		const chunks = [];
		let settled = false;
		const finish = (value) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			stdin.removeAllListeners();
			resolve(value);
		};
		const timer = setTimeout(() => finish(Buffer.concat(chunks).toString('utf8')), limitMs);
		stdin.on('data', (chunk) => chunks.push(chunk));
		stdin.on('end', () => finish(Buffer.concat(chunks).toString('utf8')));
		stdin.on('error', () => finish(''));
	});
}

function tryParseJson(text) {
	if (!text) return undefined;
	const t = text.trim();
	if (!t.startsWith('{') && !t.startsWith('[')) return undefined;
	try {
		return JSON.parse(t);
	} catch {
		return undefined;
	}
}

async function collectSources() {
	const sources = [];
	const push = (text, label) => {
		if (text) sources.push({ text, parsed: tryParseJson(text), label });
	};
	for (const k of ['ANTIGRAVITY_TOOL_INPUT', 'TOOL_INPUT', 'ANTIGRAVITY_TARGET_FILE', 'TARGET_FILE']) {
		if (process.env[k]) push(process.env[k], `env:${k}`);
	}
	push(await readStdin(1500), 'stdin');
	for (const arg of process.argv.slice(2)) push(arg, 'argv');
	return sources;
}

// ---------------------------------------------------------------------------
// 대상 추정: JSON 경로 키 > 리다이렉트/유니코드 경로 정규식 > argv 존재 검사
// ---------------------------------------------------------------------------

const TARGET_KEY_RE = /^(file_path|filepath|path|target|target_file|targetfile|target_path|targetpath|output|file|filename)$/i;
// \S 기반 — 한글/공백 파일명도 매칭 (구버전 [\w\-\.] 은 ASCII 한정으로 한글 보고서를 놓쳤다)
const PATH_LIKE_RE = /([^\s"'`<>|;&]+[\/\\][^\s"'`<>|;&]+\.(?:md|html|txt|json))/i;
const REDIRECT_RE = /(?:>|>>)\s*([^\s|&;]+(?:\.(?:md|html|txt|json)))/i;
const ARGV_REPORT_RE = /\.(md|html|txt)$/;

function extractTargetFromNode(node, out) {
	if (Array.isArray(node)) {
		for (const item of node) extractTargetFromNode(item, out);
		return;
	}
	if (!node || typeof node !== 'object') return;
	for (const [key, value] of Object.entries(node)) {
		if (typeof value === 'string' && value) {
			if (TARGET_KEY_RE.test(key)) out.push({ value, isCommand: false });
			else if (/^(command|cmd|command_line|commandline|script|args)$/i.test(key)) out.push({ value, isCommand: true }); // bash 리다이렉트 대상
		} else if (value && typeof value === 'object') {
			extractTargetFromNode(value, out);
		}
	}
}

function extractTarget(sources) {
	const env = process.env.ANTIGRAVITY_TARGET_FILE || process.env.TARGET_FILE || '';
	if (env) return env;
	for (const s of sources) {
		if (s.parsed !== undefined) {
			const found = [];
			extractTargetFromNode(s.parsed, found);
			for (const { value, isCommand } of found) {
				if (!isCommand) return value; // file_path/target/output 는 그대로 대상
				// command 값은 리다이렉트/경로 정규식으로 재추출
				const red = value.match(REDIRECT_RE);
				if (red) return red[1];
				const m = value.match(PATH_LIKE_RE);
				if (m) return m[1];
			}
		}
	}
	for (const s of sources) {
		if (s.parsed !== undefined) continue;
		const red = s.text.match(REDIRECT_RE);
		if (red) return red[1];
		const m = s.text.match(PATH_LIKE_RE);
		if (m) return m[1];
	}
	for (const arg of process.argv.slice(2)) {
		if (ARGV_REPORT_RE.test(arg) && fs.existsSync(arg)) return arg;
	}
	return '';
}

const GUARD_KEYWORD_RE = /보고서|감정서|소견서|의견서|진단서|확인서|분석서|결과서|검토해줘|검증해줘|검증|할루시네이션|할루체크|팩트체크|거짓말검사|사실확인|무결성검사|verify|\/verify|\/할루체크|\/검증|법적.*검토|요약해줘|요약파일|요약 파일|기록해줘|기록해|정리해줘|정리해|결론정리|summary|findings|\/요약|\/기록/i;
const REPORT_PATH_HINT_RE = /report|보고서|감정서|소견서|의견서|진단서|확인서|분석서|결과서|draft|초안|analysis|opinion|summary|findings|notes|요약|기록/i;
const READ_CAP_BYTES = 2 * 1024 * 1024; // shouldGuard 내용 훑기 상한

function shouldGuard(targetFile, rawTexts) {
	const keywordHit = GUARD_KEYWORD_RE.test(rawTexts.join('\n'));
	const reportExt = /\.(md|html|txt)$/i.test(targetFile);
	const reportPathHint = REPORT_PATH_HINT_RE.test(targetFile);
	if (keywordHit && reportExt) return true;
	if (reportPathHint && reportExt) return true;
	if (reportExt && fs.existsSync(targetFile)) {
		try {
			const fd = fs.openSync(targetFile, 'r');
			const buf = Buffer.alloc(Math.min(READ_CAP_BYTES, fs.fstatSync(fd).size));
			fs.readSync(fd, buf, 0, buf.length, 0);
			fs.closeSync(fd);
			const text = buf.toString('utf-8');
			// 본문 폴백: 포렌식 기술 패턴 조합 시 발동.
			// 1) 64hex 해시가 본문에 있으면 길이 불문 무조건 발동 — 짧은
			//    "요약" 파일에 해시를 끼워 넣는 우회를 원천 차단한다.
			// 2) 그 외: 200자 이상 + 마커 2개 이상 동시 존재.
			// 3) 기존 폴백 유지: 500자 이상 + 사건/감정/해시/증거 키워드.
			if (/[a-fA-F0-9]{64}/.test(text)) return true;
			if (text.length > 200) {
				const forensicMarkers = [
					/SHA-?256|MD5/i.test(text),
					/감정|증거|사건|포렌식|타임스탬프|timestamp/i.test(text),
				];
				if (forensicMarkers.filter(Boolean).length >= 2) return true;
				if (text.length > 500 && /(사건|감정|해시|SHA-256|증거)/.test(text)) return true;
			}
		} catch {}
	}
	return false;
}

function ledgerCandidate(targetFile) {
	const dir = path.dirname(path.resolve(targetFile));
	const candidates = [
		join(dir, 'claim-ledger.md'),
		join(dir, 'claim_ledger.md'),
		join(process.cwd(), 'claim-ledger.md'),
		join(process.cwd(), 'claim_ledger.md'),
	];
	return candidates.find((p) => fs.existsSync(p));
}

// evidence 자동 탐색: audit.json 3곳 + 이 플러그인 자신이 기록하는 감사 로그
function evidenceCandidates(targetFile) {
	const dir = path.dirname(path.resolve(targetFile));
	const candidates = [
		join(dir, 'audit.json'),
		join(dir, '.lazyforensic', 'audit_trail.jsonl'),
		join(process.cwd(), 'audit.json'),
		join(process.cwd(), '.lazyforensic', 'audit_trail.jsonl'),
		join(root, 'audit.json'),
		join(root, '.lazyforensic', 'audit_trail.jsonl'),
	];
	return candidates.filter((p) => fs.existsSync(p));
}

// timeline 자동 탐색: 보고서 옆/작업 디렉터리의 timeline json — 시각 grounding
function timelineCandidates(targetFile) {
	const dir = path.dirname(path.resolve(targetFile));
	const candidates = [
		join(dir, 'timeline.json'),
		join(dir, 'timeline_events.json'),
		join(dir, 'events.json'),
		join(process.cwd(), 'timeline.json'),
		join(process.cwd(), 'timeline_events.json'),
		join(process.cwd(), 'events.json'),
	];
	return candidates.find((p) => fs.existsSync(p));
}

// Python 탐색: OS별 최적 순서 및 Windows Store alias 오탐 배제
const PYTHON_CANDIDATES = process.platform === 'win32'
	? [
		{ cmd: 'python', pre: [] },
		{ cmd: 'py', pre: ['-3'] },
		{ cmd: 'python3', pre: [] },
	]
	: [
		{ cmd: 'python3', pre: [] },
		{ cmd: 'python', pre: [] },
		{ cmd: 'py', pre: ['-3'] },
	];

function runVerify(args) {
	const attempts = [];
	for (const { cmd, pre } of PYTHON_CANDIDATES) {
		const res = spawnSync(cmd, [...pre, ...args], { cwd: root, encoding: 'utf-8', windowsHide: true });
		if (res.error) {
			if (res.error.code === 'ENOENT') {
				attempts.push(`${cmd}: not found`);
				continue;
			}
			return { res, cmd, spawnError: true, attempts };
		}
		if (res.status !== 0 && (res.status === 9009 || /Python was not found|No Python installation|^Python\s*$/i.test((res.stderr || '').trim()))) {
			// Windows Store app-execution alias — 진짜 인터프리터가 아니다
			attempts.push(`${cmd}: store alias`);
			continue;
		}
		return { res, cmd, spawnError: false, attempts };
	}
	return { res: null, cmd: null, spawnError: false, attempts };
}

async function main() {
	const sources = await collectSources();
	const rawTexts = sources.map((s) => s.text);
	const targetFile = extractTarget(sources);

	if (!targetFile || !fs.existsSync(targetFile)) {
		process.exit(0); // 가드 대상 없음 — 오탐 방지
	}
	if (!shouldGuard(targetFile, rawTexts)) {
		process.exit(0);
	}

	const verifyScript = join(root, 'scripts', 'verify_report.py');
	if (!fs.existsSync(verifyScript)) {
		process.exit(0);
	}

	const evidence = evidenceCandidates(targetFile);
	const ledger = ledgerCandidate(targetFile);
	const timeline = timelineCandidates(targetFile);
	const args = [verifyScript, targetFile];
	if (evidence.length > 0) {
		args.push('--evidence', ...evidence, '--morph-grounding');
	}
	if (timeline) {
		args.push('--timeline', timeline);
	}
	if (ledger) {
		args.push('--claim-ledger', ledger);
	}

	const { res, spawnError, attempts } = runVerify(args);

	if (res === null) {
		// 인터프리터 전무 — 검증 자체가 불가능하다. 모든 쓰기를 막으면 플러그인이
		// 사용 불능이 되므로 경고 후 통과(FAIL_OPEN). 설치를 안내한다.
		console.error('[HALLUCINATION GUARD] 검증 스킵 — python 실행 환경을 찾지 못했다:');
		for (const a of attempts) console.error(`  - ${a}`);
		console.error('[HINT] python3 설치 후 이 게이트가 자동으로 다시 작동한다. 설치 전에는 보고서 검증이 수동으로만 가능하다:');
		console.error(`[CMD] python scripts/verify_report.py "${targetFile}" --json`);
		process.exit(0);
	}
	if (spawnError) {
		console.error('[HALLUCINATION GUARD] 검증 실행 실패 (spawn 오류) — FAIL_CLOSED');
		console.error(String(res.error || '').slice(0, 600));
		process.exit(1);
	}

	if (res.status === 2) {
		// verify_report.py: 보고서를 찾지 못함 — 대상 추정이 틀렸을 가능성. 차단하지 않고 알린다.
		console.error(`[HALLUCINATION GUARD] 검증 대상 미발견(스킵) — 추정 경로가 틀렸을 수 있음: ${targetFile}`);
		process.exit(0);
	}
	if (res.status !== 0) {
		const out = ((res.stderr || '') + '\n' + (res.stdout || '')).toString().trim();
		console.error(`[HALLUCINATION GUARD] 보고서 검증 FAIL — "${path.basename(targetFile)}" (PostToolUse 사후 게이트, 보고서/검토해줘 트리거)`);
		console.error(out.slice(0, 1200));
		console.error('[HINT] 금지문구 제거, 해시는 audit.json / .lazyforensic/audit_trail.jsonl 근거만 사용, 없으면 미확인/미측정으로 둘 것. 수정 후 재시도.');
		console.error(`[CMD] python scripts/verify_report.py "${targetFile}" --evidence <audit.json|audit_trail.jsonl> --json`);
		process.exit(1);
	}

	if (res.stdout && res.stdout.includes('[WARN]')) {
		console.error(`[HALLUCINATION GUARD] WARN — ${path.basename(targetFile)}: ${res.stdout.slice(0, 600)}`);
	}
	process.exit(0);
}

main();
