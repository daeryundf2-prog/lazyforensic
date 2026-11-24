"""B2-1 오탐 방지: --law-cache 조건부 FAIL 승격 회귀 테스트.

- strict + 캐시미존재(조문번호 없음) -> FAIL (errors 편입)
- strict + 캐시존재(조문번호 있음) -> WARN 유지 (errors 승격 없음)
- 기본(strict 없음) + 캐시미존재 -> WARN (errors 편입 없음)

상한 위반(예: 개인정보보호법 제999조)과 무관한 범위 내 조문(제70조/제32조의2)으로
법령 캐시 판정만 격리 검증한다.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_verify(report_text: str, cache_data: dict, extra_args: list[str]):
    tmpdir = tempfile.TemporaryDirectory()
    tmp = Path(tmpdir.name)
    # TemporaryDirectory lifetime: keep ref via function attr
    cache_file = tmp / "law_cache.json"
    cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
    report = tmp / "report.md"
    report.write_text(report_text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_report.py"), str(report),
         "--law-cache", str(cache_file), "--json", *extra_args],
        capture_output=True, text=True, encoding="utf-8",
    )
    data = json.loads(proc.stdout)
    # attach cleanup
    proc._tmpdir = tmpdir  # noqa: SLF001 - keep temp alive until caller done
    return proc, data, tmpdir


CACHE = {"statute": "개인정보보호법", "articles": ["제32조의2", "제71조"]}


class LawCacheBindingTests(unittest.TestCase):
    def test_strict_missing_escalates_to_fail(self):
        # strict + 캐시없음(제70조는 상한 76 이내이나 캐시에 없음) -> FAIL
        proc, data, _td = run_verify(
            "개인정보보호법 제70조 위반이다.\n", CACHE, ["--strict"],
        )
        try:
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(data["verdict"], "FAIL")
            self.assertTrue(
                any("법령 캐시" in e and "존재하지 않음" in e for e in data.get("errors", [])),
                f"errors missing law-cache FAIL: {data}",
            )
        finally:
            _td.cleanup()

    def test_strict_present_stays_warn(self):
        # strict + 캐시있음(제32조의2는 캐시에 있음) -> WARN 유지, errors 승격 없음
        proc, data, _td = run_verify(
            "개인정보보호법 제32조의2 위반이다.\n", CACHE, ["--strict"],
        )
        try:
            self.assertEqual(data["verdict"], "WARN", f"expected WARN, got: {data}")
            self.assertFalse(
                any("법령 캐시" in e for e in data.get("errors", [])),
                f"law-cache must not escalate to errors when present: {data}",
            )
            self.assertEqual(proc.returncode, 1)  # --strict는 WARN이어도 exit 1
        finally:
            _td.cleanup()

    def test_default_missing_stays_warn(self):
        # 기본(strict 없음) + 캐시없음 -> WARN, errors 편입 없음
        proc, data, _td = run_verify(
            "개인정보보호법 제70조 위반이다.\n", CACHE, [],
        )
        try:
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(data["verdict"], "WARN", f"expected WARN, got: {data}")
            self.assertTrue(
                any("법령 캐시" in w and "존재하지 않음" in w for w in data.get("warnings", [])),
                f"warnings missing law-cache WARN: {data}",
            )
            self.assertFalse(
                any("법령 캐시" in e for e in data.get("errors", [])),
                f"law-cache must stay WARN without --strict: {data}",
            )
        finally:
            _td.cleanup()


def verify_with_cache(tmp_path, report_text, cache_bytes, *extra):
    cache_file = tmp_path / "law_cache.json"
    cache_file.write_bytes(cache_bytes)
    report = tmp_path / "report.md"
    report.write_text(report_text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_report.py"), str(report),
         "--law-cache", str(cache_file), "--json", *extra],
        capture_output=True, text=True, encoding="utf-8",
    )
    return proc, json.loads(proc.stdout)


EDITION = {"statute_name": "개인정보 보호법", "law_id": "SYN-0001", "effective_date": "2023-09-15",
           "articles": {"제32조의2": "① 합성 문장 ② 합성 문장", "71": "제70조를 위반한 자 (합성 본문)"}}


def test_same_parsed_cache_gets_the_same_verdict_regardless_of_json_notation(tmp_path):
    # D13 #9: raw Hangul, \\u escapes, indentation and key order deserialize to the same object.
    obj = {"articles": ["제32조의2"]}
    notations = [json.dumps(obj, ensure_ascii=False).encode(), json.dumps(obj, ensure_ascii=True).encode(),
                 json.dumps(obj, ensure_ascii=True, indent=4).encode(), b"\xef\xbb\xbf" + json.dumps(obj).encode()]
    results = []
    for index, data in enumerate(notations):
        folder = tmp_path / str(index)
        folder.mkdir()
        results.append(verify_with_cache(folder, "합성 대조 자료. 제32조의2.\n", data)[1])
    assert {(r["verdict"], tuple(r["warnings"])) for r in results} == {("PASS", ())}
    assert all(r["law_cache"]["citations"][0]["status"] == "matched" for r in results)


def test_structured_match_records_law_edition_article_and_paragraph(tmp_path):
    proc, result = verify_with_cache(
        tmp_path, "「개인정보 보호법」(2023. 9. 15. 시행) 제32조의2 제2항 위반 여부를 검토한다.\n",
        json.dumps(EDITION, ensure_ascii=True).encode())
    assert proc.returncode == 0 and result["verdict"] == "PASS", result
    item, = result["law_cache"]["citations"]
    assert item["status"] == "matched"
    assert item["basis"] == {"law": "개인정보 보호법", "law_id": "SYN-0001", "effective_date": "2023-09-15",
                             "article": "제32조의2", "paragraph": 2}
    assert result["law_cache"]["cache_sha256"] == hashlib.sha256((tmp_path / "law_cache.json").read_bytes()).hexdigest()


@pytest.mark.parametrize(("report", "reason"), [
    ("형법 제32조의2 위반 여부를 검토한다.\n", "다른 법령"),                      # 다른 법령의 동번호 조문
    ("개인정보보호법(2020. 8. 5. 시행) 제32조의2를 검토한다.\n", "시행일"),     # 다른 시점의 동번호 조문
    ("개인정보보호법 제32조의2 제3항을 검토한다.\n", "제3항"),                   # 캐시에 없는 항
])
def test_same_number_from_another_law_edition_or_paragraph_stays_unconfirmed(tmp_path, report, reason):
    proc, result = verify_with_cache(tmp_path, report, json.dumps(EDITION, ensure_ascii=False).encode())
    assert result["verdict"] == "WARN" and proc.returncode == 0, result
    item, = result["law_cache"]["citations"]
    assert item["status"] == "unconfirmed" and reason in item["reason"]
    assert any("특정하지 못함" in w and reason in w for w in result["warnings"])
    # The source-marker warning is not lifted by an unconfirmed citation.
    assert any("korean_law MCP 출처 표기 없음" in w for w in result["warnings"])


def test_article_shared_by_two_editions_needs_the_cited_edition(tmp_path):
    cache = {"laws": [{"statute": "개인정보보호법", "시행일자": "20200805", "articles": ["제32조의2"]},
                      {"statute": "개인정보보호법", "시행일자": "20230915", "articles": ["제32조의2"]}]}
    data = json.dumps(cache, ensure_ascii=True).encode()
    _, ambiguous = verify_with_cache(tmp_path, "개인정보보호법 제32조의2를 검토한다.\n", data)
    assert ambiguous["verdict"] == "WARN" and any("특정하지 못함" in w for w in ambiguous["warnings"])
    assert ambiguous["law_cache"]["citations"][0]["status"] == "unconfirmed"
    _, cited = verify_with_cache(tmp_path, "개인정보보호법(2023. 9. 15. 시행) 제32조의2를 검토한다.\n", data)
    assert cited["verdict"] == "PASS", cited
    assert cited["law_cache"]["citations"][0]["basis"]["effective_date"] == "2023-09-15"


def test_article_mentioned_only_inside_another_articles_text_is_not_evidence(tmp_path):
    # 제71조 본문이 제70조를 언급해도 제70조 레코드는 아니다 (JSON 원문 부분 문자열 대조 금지).
    _, result = verify_with_cache(tmp_path, "개인정보 보호법 제70조를 검토한다.\n",
                                  json.dumps(EDITION, ensure_ascii=False).encode())
    assert any("존재하지 않음" in w for w in result["warnings"])
    assert result["law_cache"]["citations"][0]["status"] == "missing"


@pytest.mark.parametrize(("label", "article", "status"), [
    # D17 F7: a circled numeral quoted as an example inside the text is not a paragraph start.
    ("circle in quotation", {"article": "제2조", "paragraphs": [1], "text": "① 표시 예시는 ‘②’이다."}, "unconfirmed"),
    ("same text, no list", {"article": "제2조", "text": "① 표시 예시는 ‘②’이다."}, "unconfirmed"),
    ("out of sequence", {"article": "제2조", "text": "① 합성 본문 ③ 합성 본문"}, "unconfirmed"),
    ("no circle", {"article": "제2조", "paragraphs": [1], "text": "① 표시 예시"}, "unconfirmed"),
    ("structured", {"article": "제2조", "paragraphs": [1, 2], "text": "합성 본문"}, "matched"),
    ("paragraph starts", {"article": "제2조", "text": "제2조(정의) ① 합성 본문.\n② 합성 본문"}, "matched"),
    ("paragraph items", {"article": "제2조", "paragraphs": [{"text": "① 합성"}, {"text": "② 합성"}]}, "matched"),
])
def test_circled_numeral_counts_only_as_a_paragraph_start(tmp_path, label, article, status):
    cache = {"law_name": "합성법", "law_id": "SYN", "articles": [article]}
    _, result = verify_with_cache(tmp_path, "합성법 제2조 제2항을 검토한다.\n", json.dumps(cache, ensure_ascii=False).encode())
    item, = result["law_cache"]["citations"]
    assert item["status"] == status, (label, item)
    if status == "unconfirmed":
        assert "제2항" in item["reason"] and result["verdict"] == "WARN", result


if __name__ == "__main__":
    unittest.main()
