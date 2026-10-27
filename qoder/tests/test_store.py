"""Guards for the one place Recap builds SQL by hand.

`pylibseekdb`'s cursor accepts no bind parameters, so the hybrid-search statement is
assembled from whitelisted literals. These tests pin the whitelist shut: the only
way to get a quote, a backslash or a semicolon into the statement is to raise.
"""

from datetime import date

import pytest

from recap.store import Doc, sql_date, sql_text, sql_vector


class TestSqlText:
    @pytest.mark.parametrize(
        "value",
        [
            "verified",
            "seekdb",
            "recap.snapshot",
            "本周 提交 commit 进展",
            "hybrid_search (vector + full-text) [2026-W44]",
            "# 周报 2026-W44",
            "",
        ],
    )
    def test_accepts_plain_text(self, value: str) -> None:
        assert sql_text(value) == f"'{value}'"

    @pytest.mark.parametrize(
        "value",
        [
            "' OR 1=1 --",
            "x' UNION SELECT * FROM c$v2$0",
            "a; DROP TABLE documents",
            "back\\slash",
            '"double quoted"',
            "`; shell`",
            "x" * 201,
        ],
    )
    def test_refuses_breakout_attempts(self, value: str) -> None:
        with pytest.raises(ValueError):
            sql_text(value)

    def test_refusal_leaves_no_partial_literal(self, value: str = "'; --") -> None:
        """A rejected phrase must never reach the statement, not even unquoted."""
        with pytest.raises(ValueError):
            sql_text(value)

    def test_whitelist_is_tight_by_design(self) -> None:
        """In practice the only caller passes an ASCII identifier taken from the corpus
        (`web_export._top_token`), so typographic punctuation is refused, not escaped."""
        with pytest.raises(ValueError):
            sql_text("周报 · 2026-W44")


class TestSqlDate:
    def test_accepts_date_object(self) -> None:
        assert sql_date(date(2026, 10, 27)) == "'2026-10-27'"

    def test_accepts_iso_string(self) -> None:
        assert sql_date("2026-10-27") == "'2026-10-27'"

    @pytest.mark.parametrize(
        "value",
        ["2026-10-27' OR '1", "2026-10-2", "27-10-2026", "tomorrow", "2026-13-45"],
    )
    def test_refuses_non_iso(self, value) -> None:
        with pytest.raises(ValueError):
            sql_date(value)


class TestSqlVector:
    def test_emits_numeric_literal(self) -> None:
        assert sql_vector([0.1, -2.5, 3.0]) == "'[0.1,-2.5,3.0]'"

    def test_coerces_ints_and_rejects_text(self) -> None:
        with pytest.raises(ValueError):
            sql_vector(["0.1); DROP TABLE x"])
        assert sql_vector([1, 2]) == "'[1.0,2.0]'"


def test_doc_metadata_carries_integer_window_key() -> None:
    doc = Doc(
        id="x",
        text="t",
        family="source",
        kind="evidence",
        ref="source/x",
        created_at=date(2026, 10, 26),
    )
    meta = doc.metadata()
    assert meta["created_at"] == "2026-10-26"
    # seekdb rejects range comparisons on JSON strings, so the filter uses this key.
    assert meta["created_key"] == 20261026
