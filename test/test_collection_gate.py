import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from functools import partial
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = load_module("collection_gate", "scripts/collection_gate.py")
osint = load_module("osint_username_gate", "scripts/osint_username.py")
archive = load_module("archive_survey_gate", "scripts/archive_survey.py")


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.now += sec


class GatePrimitivesTests(unittest.TestCase):
    def test_cap_never_exceeds_fifty(self):
        taken, skipped = gate.cap_items(list(range(60)), cap=500)
        self.assertEqual(len(taken), 50)
        self.assertEqual(skipped, list(range(50, 60)))

    def test_lower_user_cap_is_honored(self):
        taken, skipped = gate.cap_items(["a", "b", "c"], cap=2)
        self.assertEqual((taken, skipped), (["a", "b"], ["c"]))

    def test_scope_must_be_one_nonempty_line(self):
        for bad in (None, "", "   ", "a\nb"):
            with self.assertRaises(ValueError):
                gate.validate_scope(bad)
        self.assertEqual(gate.validate_scope(" 대상 1건 "), "대상 1건")

    def test_rate_limiter_spaces_requests_one_second(self):
        fc = FakeClock()
        rl = gate.RateLimiter(clock=fc.clock, sleep=fc.sleep)
        rl.wait()
        rl.wait()
        self.assertEqual(fc.sleeps, [1.0])

    def test_first_429_halves_rate_second_stops(self):
        fc = FakeClock()
        rl = gate.RateLimiter(clock=fc.clock, sleep=fc.sleep)
        self.assertTrue(rl.on_429(retry_after=5))
        self.assertEqual(fc.sleeps, [5])
        self.assertEqual(rl.interval, 2.0)
        self.assertFalse(rl.on_429())
        self.assertTrue(rl.stopped)

    def test_robots_disallow_is_respected(self):
        robots = "User-agent: *\nDisallow: /private/\n"
        self.assertFalse(gate.robots_allows(robots, "https://x.test/private/a"))
        self.assertTrue(gate.robots_allows(robots, "https://x.test/public/a"))


class OsintGateTests(unittest.TestCase):
    def run_main(self, argv, results):
        fc = FakeClock()
        limiter = partial(gate.RateLimiter, clock=fc.clock, sleep=fc.sleep)
        seen = []

        def fake_run(cmd, name, timeout):
            seen.append(name)
            return results(name)

        buf = io.StringIO()
        with mock.patch.object(osint, "find_sherlock", return_value=["sherlock"]), \
                mock.patch.object(osint, "run_sherlock", side_effect=fake_run), \
                mock.patch.object(osint.gate, "RateLimiter", limiter), \
                contextlib.redirect_stdout(buf):
            code = osint.main(argv)
        return code, json.loads(buf.getvalue()), seen, fc

    def ok(self, name):
        return {"username": name, "found_count": 0, "urls": [], "stderr": "", "rate_limited": False}

    def test_missing_scope_exits_2_before_any_request(self):
        with mock.patch.object(osint, "run_sherlock") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(osint.main(["a"]), 2)
        run.assert_not_called()

    def test_caps_at_fifty_and_records_skipped(self):
        names = [f"u{i}" for i in range(60)]
        code, out, seen, fc = self.run_main(names + ["--json", "--scope", "60명, 상한 50"], self.ok)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 50)
        self.assertEqual(out["skipped"], names[50:])
        self.assertEqual(out["status"], "capped")
        self.assertEqual(fc.sleeps.count(1.0), 49)

    def test_second_429_stops_with_fetched_partial(self):
        def limited(name):
            r = self.ok(name)
            r["rate_limited"] = True
            return r

        code, out, seen, fc = self.run_main(["a", "b", "c", "d", "--json", "--scope", "4명"], limited)
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(out["status"], "fetched_partial")
        self.assertEqual(out["skipped"], ["c", "d"])
        self.assertEqual(out["interval_sec"], 2.0)


class ArchiveGateTests(unittest.TestCase):
    def make_zips(self, root, n):
        for i in range(n):
            with zipfile.ZipFile(root / f"a{i:03d}.zip", "w") as z:
                z.writestr("note.txt", "x")

    def test_scan_caps_at_fifty_and_records_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_zips(root, 52)
            skipped = []
            records = archive.scan(root, skipped=skipped)
            self.assertEqual(len(records), 50)
            self.assertEqual([Path(s).name for s in skipped], ["a050.zip", "a051.zip"])

    def test_cap_argument_cannot_raise_above_fifty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_zips(root, 51)
            self.assertEqual(len(archive.scan(root, max_items=500)), 50)

    def test_main_json_reports_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_zips(root, 51)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                archive.main([str(root), "--json"])
            self.assertEqual(len(json.loads(buf.getvalue())["skipped"]), 1)


if __name__ == "__main__":
    unittest.main()
