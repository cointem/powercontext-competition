"""Trace reading is the only input Recap has, so its parsing rules are pinned here."""

import subprocess
from datetime import date

import pytest

from recap import traces
from recap.traces import Commit, Note, _parse_memories, git_commits


class FakeRun:
    """Records the git commands and answers `log` / `show --name-only` separately."""

    def __init__(self, log_out: str, files: int = 3) -> None:
        self.log_out = log_out
        self.files = files
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=True, text=True, **kw) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        out = self.log_out if "log" in cmd else "\n".join(f"f{i}.py" for i in range(self.files))
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> str:
    (tmp_path / ".git").mkdir()
    return str(tmp_path)


def test_window_widens_until_by_one_day(repo, monkeypatch) -> None:
    fake = FakeRun("")
    monkeypatch.setattr(traces.subprocess, "run", fake)
    git_commits(repo, "cointem", date(2026, 10, 20), date(2026, 10, 26))
    log = next(" ".join(c) for c in fake.calls if "log" in c)
    assert "--since=2026-10-20" in log
    assert "--until=2026-10-27" in log


def test_parses_records_with_empty_body(repo, monkeypatch) -> None:
    # A body-less commit still has to fill all seven positional columns; splitting on a
    # stripped record would shift every column left and mis-parse the subject as author.
    record = "\x1f".join(
        ["", "a" * 40, "abcdef1", "2026-10-23T09:00:00+08:00", "cointem", "feat: add indexer", ""]
    )
    fake = FakeRun(record + "\x1e")
    monkeypatch.setattr(traces.subprocess, "run", fake)
    commits = git_commits(repo, "cointem", date(2026, 10, 20), date(2026, 10, 26), repo_label="recap")
    assert len(commits) == 1
    c = commits[0]
    assert (c.repo, c.short, c.author, c.subject, c.when) == (
        "recap",
        "abcdef1",
        "cointem",
        "feat: add indexer",
        date(2026, 10, 23),
    )
    assert c.files == 3


def test_keeps_separators_inside_body(repo, monkeypatch) -> None:
    body = "uses \x1f-free prose\nsecond line"
    record = "\x1f".join(
        ["", "b" * 40, "1234567", "2026-10-24T09:00:00+08:00", "cointem", "fix: parse", body]
    )
    monkeypatch.setattr(traces.subprocess, "run", FakeRun(record + "\x1e"))
    commits = git_commits(repo, "cointem", date(2026, 10, 20), date(2026, 10, 26))
    assert commits[0].body == body


def test_skips_non_git_path(tmp_path) -> None:
    assert git_commits(str(tmp_path), "cointem", date(2026, 10, 20), date(2026, 10, 26)) == []


class TestParseMemories:
    def test_reads_english_and_chinese_tags(self) -> None:
        text = "\n".join(
            [
                "- [decision] 采用 seekdb 作为检索层",
                "- [constraint] 不动端口 12883 上的 seekdb 实例",
                "- [next_step] 补齐验收材料",
                "- [决定] 等价写法",
                "- [约束] 中文约束",
                "  - [下一步] 缩进也要认",
            ]
        )
        assert _parse_memories(text) == [
            ("decision", "采用 seekdb 作为检索层"),
            ("constraint", "不动端口 12883 上的 seekdb 实例"),
            ("next_step", "补齐验收材料"),
            ("decision", "等价写法"),
            ("constraint", "中文约束"),
            ("next_step", "缩进也要认"),
        ]

    def test_ignores_unknown_tags_and_empty_bodies(self) -> None:
        text = "\n".join(["- [fact] ignored, not a memory kind", "- [decision]", "- plain bullet"])
        assert _parse_memories(text) == []


class TestAsText:
    def test_note_with_own_heading_is_not_prefixed_twice(self) -> None:
        note = Note(
            origin="notes/week.md",
            title="Recap 设计",
            text="# Recap 设计\n\n正文",
            when=date(2026, 10, 26),
            memories=[],
        )
        assert note.as_text == "# Recap 设计\n\n正文"

    def test_note_without_heading_gets_one(self) -> None:
        note = Note(origin="n.md", title="标题", text="正文", when=date(2026, 10, 26), memories=[])
        assert note.as_text == "# 标题\n\n正文"

    def test_commit_text_is_self_containing(self) -> None:
        commit = Commit(
            repo="recap",
            sha="c" * 40,
            short="ccccccc",
            when=date(2026, 10, 23),
            author="cointem",
            subject="feat: index",
            body="mirror into seekdb",
            files=4,
        )
        text = commit.as_text
        assert text.startswith("[recap] feat: index")
        assert "commit " + "c" * 40 in text
        assert "4 files changed" in text
        assert commit.label == f"commit:recap:{'c' * 40}"


def test_notes_filters_by_mtime(tmp_path) -> None:
    (tmp_path / "in.md").write_text("# in\n\n- [decision] keep")
    (tmp_path / "out.md").write_text("# out")
    import os
    from datetime import datetime

    # Both mtimes are pinned: a fresh file's mtime is "today", which drifts out of a
    # fixed window as soon as the clock crosses midnight.
    inside = datetime(2026, 10, 23, 12).timestamp()
    os.utime(tmp_path / "in.md", (inside, inside))
    old = 1_600_000_000  # 2020-09-13, far outside the week
    os.utime(tmp_path / "out.md", (old, old))
    found = traces.notes([str(tmp_path)], date(2026, 10, 20), date(2026, 10, 26))
    assert [n.origin for n in found] == ["in.md"]
    assert found[0].memories == [("decision", "keep")]
