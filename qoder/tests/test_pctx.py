"""Request-shaping rules that make Recap's PowerContext writes replayable.

Each rule here exists because the server pushed back once: Source identity is
immutable (409), omissions must be cited before commit (422), and the artifact
listing is digest-only.
"""

import json

from recap.pctx import (
    PREPARED_BEGIN,
    PREPARED_END,
    _cite_declared,
    _claim,
    content_key,
    parse_prepared,
    ref_key,
)


class TestContentKey:
    def test_same_label_and_payload_replay_to_one_id(self) -> None:
        a = content_key("commit:recap:abc", {"subject": "feat", "files": 3})
        b = content_key("commit:recap:abc", {"files": 3, "subject": "feat"})
        assert a == b  # key order must not mint a second journal entry

    def test_changed_payload_is_a_new_version(self) -> None:
        assert content_key("note:n.md", "第一版") != content_key("note:n.md", "第二版")

    def test_different_label_never_collides(self) -> None:
        assert content_key("note:a.md", "same") != content_key("note:b.md", "same")

    def test_is_a_valid_source_id(self) -> None:
        key = content_key("x", "y")
        assert key.startswith("src-") and len(key) == len("src-") + 20

    def test_counts_non_string_payloads_deterministically(self) -> None:
        import datetime

        payload = {"when": datetime.date(2026, 10, 26), "n": 3}
        assert content_key("l", payload) == content_key("l", dict(payload))


class TestCiteDeclared:
    def test_wraps_bare_omissions_in_the_boundary_citation(self) -> None:
        cited = {"text": "已经有出处", "citation": {"kind": "source", "source_ref": {"source_id": "src-old"}}}
        prepared = {"content": {"omissions": ["未提交的本机草稿", cited]}}
        _cite_declared(prepared, "src-boundary")
        omissions = prepared["content"]["omissions"]
        assert omissions[0]["text"] == "未提交的本机草稿"
        assert omissions[0]["citation"] == {
            "kind": "source",
            "source_ref": {"name": "content", "source_id": "src-boundary"},
        }
        # A claim that already carries a citation is not re-stamped with a weaker one.
        assert omissions[1] is cited

    def test_is_noop_without_omissions(self) -> None:
        prepared = {"content": {"objective": "2026-W44 当前工作状态"}}
        _cite_declared(prepared, "src-boundary")
        assert prepared["content"]["omissions"] == []

    def test_claim_citations_point_at_sources(self) -> None:
        claim = _claim({"text": "feat: index", "basis": "verified", "evidence": [{"source_id": "src-1"}]})
        dumped = claim.model_dump(by_alias=True)
        assert dumped["text"] == "feat: index"
        assert dumped["evidence"][0]["source_ref"]["source_id"] == "src-1"


class TestParsePrepared:
    def test_reads_the_json_package(self) -> None:
        body = json.dumps({"items": [{"kind": "memory", "text": "x"}]})
        content = f"prefix\n{PREPARED_BEGIN}\n{body}\n{PREPARED_END}\nsuffix"
        assert parse_prepared(content) == [{"kind": "memory", "text": "x"}]

    def test_returns_empty_for_missing_or_broken_payload(self) -> None:
        assert parse_prepared(None) == []
        assert parse_prepared("no markers here") == []
        assert parse_prepared(f"{PREPARED_BEGIN}\nnot json\n{PREPARED_END}") == []


def test_ref_key_matches_powercontext_addressing() -> None:
    assert ref_key("handoff", "handoff", 8) == "handoff/handoff@8"
    assert ref_key("atomic-memory", "am_1", 2) == "atomic-memory/am_1@2"
