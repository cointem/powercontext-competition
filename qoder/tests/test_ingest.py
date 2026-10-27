"""Idempotency of the collect pipeline, checked without a server.

PowerContext journals every write, so re-running a week is only safe if Recap replays
instead of appending. These tests pin the two guards that make that true: content-keyed
Source ids and the "already committed" comparison that keeps the Handoff on one revision.
"""

import asyncio
from datetime import date

import pytest

from recap import traces
from recap.config import Config, RepoTrace
from recap.ingest import RepoBundle, _already_committed, _snapshot_week, _texts, collect, week_label

WEEK = "2026-W44"


def make_commit(subject: str, sha: str = "a" * 40, short: str = "aaaaaaa") -> traces.Commit:
    return traces.Commit(
        repo="recap", sha=sha, short=short, when=date(2026, 10, 23), author="cointem",
        subject=subject, body="", files=2,
    )


class RecordingPC:
    def __init__(self, handoffs=()) -> None:
        self._handoffs = list(handoffs)
        self.calls: list[str] = []
        self.captured: dict[str, str] = {}
        self.remembered: list[str] = []

    async def capture(self, scope_id, label, text, metadata=None):
        from recap.pctx import content_key

        self.calls.append(f"capture:{label}")
        self.captured[label] = content_key(label, text)
        return self.captured[label]

    async def remember_new(self, scope_id, kind, text, reason=""):
        if text in self.remembered:
            return None
        self.remembered.append(text)
        self.calls.append(f"remember:{kind}")
        return {"ref": f"atomic-memory/am_{text[:4]}@1", "kind": kind, "text": text}

    async def work_contract(self, scope_id, label, **kw):
        self.calls.append(f"contract:{label}")
        return {"kind": "work_contract", "source_id": f"src-{label}", "position": 1}

    async def snapshot_current_work(self, scope_id, label, objective, claims, **kw):
        self.calls.append(f"boundary:{label}")
        return {
            "boundary": {"kind": "handoff_boundary", "source_id": f"src-{label}", "position": 2},
            "prepared": {"content": {"objective": objective, "state": claims,
                                      "next_action": kw.get("next_action"),
                                      "omissions": kw.get("omissions") or []}},
        }

    async def handoffs(self, scope_id, limit=100):
        return list(self._handoffs)

    async def commit_handoff(self, scope_id, prepared, boundary_source_id):
        self.calls.append("commit")
        return {"reference": {"family": "handoff", "artifact_id": "handoff", "revision": 9},
                "source_refs": []}

    async def acknowledge(self, scope_id, label, revision, message=""):
        self.calls.append(label)
        return {"resolution": "accepted"}

    async def record_outcome(self, scope_id, label, **kw):
        self.calls.append(label)
        return {"kind": "task_outcome", "source_id": f"src-{label}", "position": 3}


def make_cfg(tmp_path) -> Config:
    return Config(
        server_url="http://127.0.0.1:8100", token="t", data_dir=tmp_path,
        seekdb_path=tmp_path / "seekdb", state_path=tmp_path / "state.json",
        pc_env_path=tmp_path / "env", repos=[RepoTrace(path="/repo", author="cointem", name="recap")],
        notes_dirs=[str(tmp_path / "notes")],
    )


@pytest.fixture
def traces_patched(monkeypatch):
    note = traces.Note(origin="n.md", title="标题", text="# 标题\n\n正文", when=date(2026, 10, 22),
                       memories=[("decision", "采用 seekdb")])
    monkeypatch.setattr(traces, "notes", lambda dirs, since, until: [note])
    commits = [make_commit("feat: index"), make_commit("fix: parse", "b" * 40, "bbbbbbb")]
    monkeypatch.setattr(traces, "git_commits", lambda *a, **kw: list(commits))
    return commits


def test_collect_writes_every_layer_once(tmp_path, traces_patched) -> None:
    pc = RecordingPC()
    result = asyncio.run(collect(make_cfg(tmp_path), pc, "scp_1", date(2026, 10, 20), date(2026, 10, 26)))
    assert result.week == WEEK
    assert result.sources == 3  # 2 commits + 1 note
    assert [b.repo for b in result.bundles] == ["recap"]
    assert result.memories[0]["text"] == "采用 seekdb"
    assert pc.calls.count("commit") == 1
    assert "ack:2026-W44" in pc.calls
    assert "outcome:recap:2026-W44" in pc.calls
    # One boundary covers the whole Scope, so every repo points at the same revision.
    assert result.bundles[0].handoff_ref["revision"] == 9


def test_recollect_replays_without_new_memory(tmp_path, traces_patched) -> None:
    pc = RecordingPC()
    asyncio.run(collect(make_cfg(tmp_path), pc, "scp_1", date(2026, 10, 20), date(2026, 10, 26)))
    second = asyncio.run(collect(make_cfg(tmp_path), pc, "scp_1", date(2026, 10, 20), date(2026, 10, 26)))
    assert second.memories == []  # already-active line is not remembered again
    assert pc.remembered == ["采用 seekdb"]
    # The same commit text must hash to the same Source id, or the journal grows a version.
    assert len(pc.captured) == 3


def test_unchanged_week_keeps_the_existing_revision() -> None:
    commit = make_commit("feat: index")
    # Exactly what _snapshot_week is about to prepare; the boundary already produced it.
    claims = [{"text": "feat: index", "basis": "verified", "evidence": [{"source_id": "src-1"}]}]
    current = [{
        "family": "handoff", "artifact_id": "handoff", "revision": 8, "ref": "handoff/handoff@8",
        "content": {
            "state": claims,
            "next_action": {"text": "继续 recap：跟进 feat: index", "citation": {}},
            "omissions": [{"text": "未覆盖未提交的本机草稿", "citation": {}}],
        },
    }]
    pc = RecordingPC(handoffs=current)
    asyncio.run(
        _snapshot_week(pc, "scp_1", _result([RepoBundle(repo="recap", commits=[commit], source_ids=["src-1"])]))
    )
    assert "commit" not in pc.calls  # replays into handoff/handoff@8 instead of minting @9
    assert not any(c.startswith("ack:") for c in pc.calls)


def test_new_commit_advances_the_revision() -> None:
    commits = [make_commit("feat: index"), make_commit("fix: parse", "b" * 40, "bbbbbbb")]
    pc = RecordingPC(handoffs=[{
        "family": "handoff", "artifact_id": "handoff", "revision": 8, "ref": "handoff/handoff@8",
        "content": {"state": [{"text": "feat: index"}], "next_action": {"text": "旧"}, "omissions": []},
    }])
    asyncio.run(_snapshot_week(pc, "scp_1", _result([RepoBundle(repo="recap", commits=commits,
                                                                source_ids=["src-1", "src-2"])])))
    assert pc.calls.count("commit") == 1
    assert "ack:2026-W44" in pc.calls


def _result(bundles):
    from recap.ingest import IngestResult

    return IngestResult(week=WEEK, since=date(2026, 10, 20), until=date(2026, 10, 26), bundles=bundles)


class TestAlreadyCommitted:
    def test_compares_text_not_wrappers(self) -> None:
        committed = {"state": [{"text": "a", "citation": {}}], "next_action": {"text": "n"},
                     "omissions": [{"text": "o", "citation": {}}]}
        fresh = {"state": [{"text": "a"}], "next_action": {"text": "n"}, "omissions": ["o"]}
        assert _already_committed(committed, fresh["state"], fresh)

    def test_changed_subject_is_not_committed(self) -> None:
        assert not _already_committed({"state": [{"text": "a"}]}, [{"text": "b"}], {"state": [{"text": "b"}]})

    def test_added_omission_is_not_committed(self) -> None:
        content = {"state": [{"text": "a"}], "omissions": []}
        prepared = {"state": [{"text": "a"}], "omissions": [{"text": "新缺口"}]}
        assert not _already_committed(content, prepared["state"], prepared)

    def test_texts_unwraps_both_shapes(self) -> None:
        assert _texts([{"text": "a"}, "b", None]) == ["a", "b", None]
        assert _texts(None) == []


def test_week_label_is_iso_week() -> None:
    assert week_label(date(2026, 10, 26)) == "2026-W44"
    assert week_label(date(2026, 10, 31)) == "2026-W44"
    assert week_label(date(2027, 1, 1)) == "2026-W53"
