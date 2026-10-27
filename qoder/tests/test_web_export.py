"""The site is a snapshot of one real run; these are the pieces that shape it."""

import json
from datetime import date

from recap.store import Doc
from recap.web_export import (
    SQL_COLUMNS,
    _elide_vector,
    _probe_queries,
    _round_distances,
    _top_token,
    seed_items,
)


class TestProbeQueries:
    def test_takes_one_line_per_section_of_this_week(self) -> None:
        report = {
            "progress": [{"text": "feat: add indexer"}, {"text": "第二条不会被当成查询"}],
            "decisions": [{"text": "采用 seekdb"}],
            "next_week": [{"text": "补齐验收材料"}],
        }
        assert _probe_queries(report) == ["feat: add indexer", "采用 seekdb", "补齐验收材料"]

    def test_skips_missing_sections_and_shortens(self) -> None:
        report = {"progress": [{"text": "长" * 200}], "decisions": [], "next_week": [{"text": "长" * 200}]}
        probes = _probe_queries(report)
        assert probes == ["长" * 120]  # identical text is asked once

    def test_empty_report_probes_nothing(self) -> None:
        assert _probe_queries({"progress": [], "decisions": [], "next_week": []}) == []


class TestTopToken:
    def test_uses_the_corpus_s_own_vocabulary(self) -> None:
        texts = ["seekdb hybrid_search works", "seekdb again", "tiny a1"]
        assert _top_token(texts) == "seekdb"

    def test_ignores_short_and_numeric_tokens(self) -> None:
        assert _top_token(["2026 10 ab .net", "seekdb seekdb"]) == "seekdb"

    def test_falls_back_when_nothing_matches(self) -> None:
        assert _top_token(["", "!!!"]) == "seekdb"
        assert _top_token([]) == "seekdb"


class TestElideVector:
    def statement(self, n: int) -> str:
        return (
            "SELECT L2_DISTANCE(embedding, '[" + ",".join(["0.1"] * n) + "]') AS vec_dist "
            "FROM `c$v2$1f1390c0` WHERE MATCH(document) AGAINST('seekdb')"
        )

    def test_collapses_the_query_embedding_only(self) -> None:
        elided = _elide_vector(self.statement(1024))
        assert "[… query embedding …]" in elided
        assert "AGAINST('seekdb')" in elided
        assert len(elided) < 300

    def test_leaves_a_real_vector_distance_readable(self) -> None:
        sql = self.statement(4)
        assert _elide_vector(sql) == sql


def test_sql_projection_columns() -> None:
    """pylibseekdb's cursor returns bare tuples with no `description`, so the
    export has to know the projection by position."""
    row = ("_id-1", "source/src-1", 1, 0.4242424242)
    projected = dict(zip(SQL_COLUMNS, row))
    assert list(projected) == ["_id", "ref", "ft_hit", "vec_dist"]
    assert _round_distances(projected)["vec_dist"] == 0.424242


def test_exported_shape_is_json_serialisable() -> None:
    payload = {
        "week": "2026-W44",
        "window": {"since": date(2026, 10, 20).isoformat(), "until": date(2026, 10, 26).isoformat()},
        "hybrid_sql": {"columns": list(SQL_COLUMNS), "row_count": 5},
    }
    assert json.loads(json.dumps(payload, ensure_ascii=False))["week"] == "2026-W44"


def doc(ref: str, kind: str, text: str, when: date = date(2026, 10, 22), repo: str = "") -> Doc:
    family = ref.split("/")[0]
    family = "memory" if family == "atomic-memory" else "source"
    return Doc(
        id=ref,
        text=text,
        family=family,
        kind=kind,
        ref=ref,
        created_at=when,
        title=text.splitlines()[0],
        repo=repo,
    )


class TestSeedItems:
    def test_commits_and_notes_carry_their_own_citation(self) -> None:
        docs = [
            doc("source/aaa111", "evidence", "[recap] feat: indexer\n\ncommit abc\n2 files changed", repo="qoder"),
            doc("source/bbb222", "note", "# 索引设计\n\n seekdb 承载检索层"),
        ]
        items = seed_items(docs, "cointem")
        assert [(i["cite"], i["kind"]) for i in items] == [("source/aaa111", "commit"), ("source/bbb222", "note")]
        assert items[0]["repo"] == "qoder"
        assert items[0]["week"] == "2026-W43"
        assert items[0]["day"] == "2026-10-22"
        assert all(i["author"] == "cointem" for i in items)

    def test_memory_kinds_survive_and_the_rest_become_notes(self) -> None:
        items = seed_items([doc("atomic-memory/m1@rev1", "decision", "决定：用 seekdb")], "cointem")
        assert items[0]["kind"] == "decision"
        # the board needs a body under the citation, so a one-liner repeats itself
        assert items[0]["body"] == "决定：用 seekdb"
        other = seed_items([doc("atomic-memory/m2@rev1", "open", "待定：站点域名")], "cointem")
        assert other[0]["kind"] == "note"

    def test_process_records_are_not_work_traces(self) -> None:
        docs = [
            doc("handoff/h1@rev1", "claim", "objective"),
            doc("report/r1@rev1", "report", "# 周报"),
        ]
        assert seed_items(docs, "cointem") == []

    def test_json_envelopes_written_back_as_sources_are_skipped(self) -> None:
        docs = [
            doc("source/eee555", "note", '{\n  "schema": "powercontext.work-contract.v1",\n  "trust": "untrusted_input"\n}'),
            doc("atomic-memory/m9@rev1", "decision", '{\n "text": "escaped json"\n}'),
        ]
        assert seed_items(docs, "cointem") == []

    def test_bodies_are_flattened_within_the_function_limits(self) -> None:
        text = "# 标题\n\n" + "行1\n\t行2\n" + "x" * 3000
        item = seed_items([doc("source/ccc333", "note", text)], "cointem")[0]
        assert "\n" not in item["body"] and "\t" not in item["body"]
        assert len(item["body"]) == 1800
        assert len(item["title"]) <= 200

    def test_unnamed_document_is_dropped(self) -> None:
        assert seed_items([doc("source/ddd444", "note", "\n\n   ")], "cointem") == []
