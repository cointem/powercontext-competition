"""The mirror is derived, never authored: what goes into seekdb is decided here."""

import asyncio
from datetime import date

from recap.indexer import (
    KIND_LABEL,
    _day_from,
    _first_line,
    _repo_tag,
    _source_kind,
    build_docs,
)


def run(coro):
    return asyncio.run(coro)


class FakePC:
    def __init__(self, memories=(), journal=(), handoffs=()) -> None:
        self._memories = list(memories)
        self._journal = list(journal)
        self._handoffs = list(handoffs)

    async def active_memories(self, scope_id, kinds=None, limit=500):
        return self._memories

    async def journal(self, scope_id, limit=200):
        return self._journal

    async def handoffs(self, scope_id, limit=100):
        return self._handoffs


class TestSourceKind:
    def test_reads_envelope_schemas(self) -> None:
        for schema, expected in [
            ("work-contract", "work-contract"),
            ("current-work-handoff", "handoff-boundary"),
            ("task-outcome", "task-outcome"),
            ("atomic-memory", "memory-entry"),
            ("something.else", "structured"),
        ]:
            payload = f'{{"schema": "powercontext.{schema}.v1"}}'
            assert _source_kind(payload) == expected

    def test_falls_back_for_text(self) -> None:
        assert _source_kind("# 标题\n正文") == "note"
        assert _source_kind("[recap] feat: x\ncommit abc") == "evidence"
        assert _source_kind("{ not json") == "evidence"


def test_day_from_picks_the_first_iso_date() -> None:
    fallback = date(2026, 10, 26)
    assert _day_from("commit authored 2026-10-23 by cointem", fallback) == date(2026, 10, 23)
    assert _day_from("no dates here", fallback) == fallback
    assert _day_from('"2026-10-24"', fallback) == date(2026, 10, 24)


def test_repo_tag_and_first_line() -> None:
    assert _repo_tag("[recap] feat: x") == "recap"
    assert _repo_tag("no tag") == ""
    assert _first_line("\n\nsecond") == "second"


def test_memory_label_is_used_as_title() -> None:
    assert KIND_LABEL["decision"] == "决定"


def test_build_docs_skips_the_memory_envelope_echo() -> None:
    """The journal keeps a JSON envelope per remembered line. The memory itself is
    already mirrored from the artifact list with a resolvable citation, so the echo
    must not become a second, uncitable hit."""
    pc = FakePC(
        memories=[
            {"ref": "atomic-memory/am_1@1", "family": "atomic-memory", "kind": "decision",
             "text": "采用 seekdb", "state": "active", "state_version": 1}
        ],
        journal=[
            {"source_id": "src-note", "content": "# 周报设计\n\n- [decision] 采用 seekdb", "position": 1},
            {"source_id": "src-echo", "content": '{"schema":"powercontext.atomic-memory.v1","text":"采用 seekdb"}',
             "position": 2},
            {"source_id": "src-commit", "content": "[recap] feat: index\ncommit authored 2026-10-23 by cointem",
             "position": 3},
        ],
        handoffs=[
            {"ref": "handoff/handoff@8", "family": "handoff", "artifact_id": "handoff", "revision": 8,
             "content": {"objective": "2026-W44 当前工作状态",
                         "state": [{"text": "feat: index", "basis": "verified"}]}}
        ],
    )
    docs = run(build_docs(pc, "scp_1", date(2026, 10, 26)))
    by_ref = {d.ref for d in docs}
    assert "source/src-echo" not in by_ref
    assert {"atomic-memory/am_1@1", "source/src-note", "source/src-commit", "handoff/handoff@8"} <= by_ref

    commit = next(d for d in docs if d.ref == "source/src-commit")
    assert (commit.kind, commit.repo) == ("evidence", "recap")
    assert commit.created_at == date(2026, 10, 23)

    note = next(d for d in docs if d.ref == "source/src-note")
    assert note.kind == "note"
    assert note.title == "# 周报设计"

    claim = next(d for d in docs if d.kind == "claim")
    assert (claim.text, claim.title) == ("feat: index", "2026-W44 当前工作状态")


def test_build_docs_keeps_only_the_latest_version_of_a_trace() -> None:
    """Editing a note and re-collecting appends a new journal version (Source content is
    immutable). The mirror must show the current text, not every rewrite."""
    old = "[recap] feat: index\ncommit " + "a" * 40 + " authored 2026-10-22 by cointem\n1 file changed"
    new = "[recap] feat: index\ncommit " + "a" * 40 + " authored 2026-10-22 by cointem\n2 files changed"
    pc = FakePC(
        journal=[
            {"source_id": "src-old-note", "content": "# 标题\n\n旧稿", "position": 1},
            {"source_id": "src-new-note", "content": "# 标题\n\n新稿", "position": 4},
            {"source_id": "src-old-commit", "content": old, "position": 2},
            {"source_id": "src-new-commit", "content": new, "position": 3},
        ]
    )
    docs = run(build_docs(pc, "scp_1", date(2026, 10, 26)))
    refs = {d.ref for d in docs}
    assert refs == {"source/src-new-note", "source/src-new-commit"}
    assert "新稿" in next(d for d in docs if d.ref == "source/src-new-note").text


def test_build_docs_empty_scope_yields_nothing() -> None:
    assert run(build_docs(FakePC(), "scp_1", date(2026, 10, 26))) == []
