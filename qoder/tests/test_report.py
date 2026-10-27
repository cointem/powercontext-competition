"""Report composition must never emit an uncitable line, and never invent one."""

import asyncio
from datetime import date

from recap.report import _bullet, _deduped, _in_window, compose, render_markdown

SINCE = date(2026, 10, 20)
UNTIL = date(2026, 10, 26)


def doc(ref, text, kind="evidence", title="", repo="", created=20261023):
    return {
        "id": ref,
        "ref": ref,
        "text": text,
        "metadata": {"ref": ref, "kind": kind, "title": title, "repo": repo, "created_key": created},
    }


def memory(ref, kind, text):
    return {"ref": ref, "kind": kind, "text": text, "family": "atomic-memory"}


class FakeIndex:
    """Answers the two windowed questions the report asks; `before` is the archive lookup."""

    def __init__(self, window=(), archive=()) -> None:
        self._window = list(window)
        self._archive = list(archive)

    def search(self, text, limit=8, before=None, after=None, families=None):
        return list(self._window) if after else list(self._archive)


class FakePC:
    def __init__(self, memories=(), handoffs=()) -> None:
        self._memories = list(memories)
        self._handoffs = list(handoffs)

    async def active_memories(self, scope_id, kinds=None, limit=500):
        return self._memories

    async def handoffs(self, scope_id, limit=100):
        return self._handoffs


def build(index, pc):
    return asyncio.run(compose("scp_1", pc, index, SINCE, UNTIL, "2026-W44"))


class TestHelpers:
    def test_dedupe_keeps_the_first_citation(self) -> None:
        items = [{"text": "same", "cite": "a@1"}, {"text": "same", "cite": "a@2"}, {"text": "other", "cite": "b@1"}]
        assert _deduped(items) == [{"text": "same", "cite": "a@1"}, {"text": "other", "cite": "b@1"}]

    def test_in_window_bounds_are_inclusive(self) -> None:
        assert _in_window(doc("x", "t", created=20261020), SINCE, UNTIL)
        assert _in_window(doc("x", "t", created=20261026), SINCE, UNTIL)
        assert not _in_window(doc("x", "t", created=20261019), SINCE, UNTIL)
        assert not _in_window({"metadata": {}}, SINCE, UNTIL)

    def test_bullet_strips_the_repo_prefix_it_renders_as_suffix(self) -> None:
        item = _bullet(doc("source/src-1", "[recap] feat: add indexer", repo="recap"))
        assert item == {"text": "feat: add indexer", "cite": "source/src-1", "repo": "recap"}

    def test_bullet_prefers_title_and_falls_back_to_citation(self) -> None:
        assert _bullet(doc("r", "正文", title="标题"))["text"] == "标题"
        assert _bullet(doc("r", "第一行\n第二行"))["text"] == "第一行"
        assert _bullet({"id": "raw", "text": "x", "metadata": {}})["cite"] == "raw"


class TestCompose:
    def test_only_work_traces_become_progress(self) -> None:
        index = FakeIndex(
            window=[
                doc("source/a", "[recap] feat: index", kind="evidence", repo="recap"),
                doc("source/b", "# 设计说明", kind="note"),
                doc("source/c", '{"schema":"powercontext.task-outcome.v1"}', kind="task-outcome"),
                doc("source/d", "窗口外", kind="evidence", created=20261019),
            ]
        )
        report = build(index, FakePC())
        assert [p["text"] for p in report["progress"]] == ["feat: index", "设计说明"]
        assert report["summary"]["progress"] == 2

    def test_repeated_text_appears_once(self) -> None:
        index = FakeIndex(window=[doc("source/a", "同一句话"), doc("source/b", "同一句话")])
        report = build(index, FakePC())
        assert len(report["progress"]) == 1

    def test_decisions_are_ordered_by_kind(self) -> None:
        pc = FakePC(
            memories=[
                memory("atomic-memory/m3@1", "state", "还没发布"),
                memory("atomic-memory/m1@1", "decision", "用 seekdb 做检索层"),
                memory("atomic-memory/m4@1", "next_step", "不属于本节"),
                memory("atomic-memory/m2@1", "constraint", "不动 12883 实例"),
            ]
        )
        report = build(FakeIndex(), pc)
        assert [(d["label"], d["text"]) for d in report["decisions"]] == [
            ("决定", "用 seekdb 做检索层"),
            ("约束", "不动 12883 实例"),
            ("状态", "还没发布"),
        ]

    def test_open_items_and_next_week_come_from_the_handoff(self) -> None:
        pc = FakePC(
            memories=[memory("atomic-memory/n@1", "next_step", "补齐验收材料")],
            handoffs=[
                {
                    "ref": "handoff/handoff@8",
                    "content": {
                        "objective": "2026-W44",
                        "next_action": {"text": "继续 recap：跟进发布"},
                        "omissions": [{"text": "未覆盖未提交的本机草稿", "citation": {}}],
                        "state": [],
                    },
                }
            ],
        )
        report = build(FakeIndex(), pc)
        assert report["open_items"] == [{"text": "未覆盖未提交的本机草稿", "cite": "handoff/handoff@8"}]
        assert {"text": "继续 recap：跟进发布", "cite": "handoff/handoff@8"} in report["next_week"]
        assert {"text": "补齐验收材料", "cite": "atomic-memory/n@1"} in report["next_week"]

    def test_blocked_handoff_is_reported_as_open(self) -> None:
        pc = FakePC(
            handoffs=[{"ref": "handoff/handoff@2", "content": {"objective": "发布站点", "disposition": "blocked"}}]
        )
        assert build(FakeIndex(), pc)["open_items"][0]["text"] == "阻塞中：发布站点"

    def test_carry_over_reads_the_archive_before_the_window(self) -> None:
        index = FakeIndex(
            window=[doc("source/a", "混合检索再次出现", title="混合检索")],
            archive=[doc("report/2026-W43", "上周已经讨论过混合检索", kind="report")],
        )
        report = build(index, FakePC())
        assert report["carry_over"] == [{"text": "上周已经讨论过混合检索", "ref": "report/2026-W43"}]
        assert report["summary"]["carry_over"] == 1

    def test_every_line_is_citable(self) -> None:
        index = FakeIndex(window=[doc("source/a", "进展"), doc("source/x", "历史", kind="note")])
        pc = FakePC(
            memories=[memory("atomic-memory/m@1", "decision", "决定")],
            handoffs=[{"ref": "handoff/handoff@8", "content": {"omissions": ["缺口"], "next_action": {"text": "下一步"}}}],
        )
        report = build(index, pc)
        for section in ("progress", "decisions", "open_items", "next_week"):
            for item in report[section]:
                assert item["cite"] in report["refs"]
        assert report["refs"] == sorted({"source/a", "source/x", "atomic-memory/m@1", "handoff/handoff@8"})

    def test_empty_scope_reports_nothing_rather_than_guessing(self) -> None:
        report = build(FakeIndex(), FakePC())
        assert report["progress"] == report["decisions"] == report["carry_over"] == []
        markdown = render_markdown(report)
        assert "窗口内没有可引用的工作痕迹。" in markdown
        assert "- 无。" in markdown

    def test_markdown_carries_the_window_and_citations(self) -> None:
        index = FakeIndex(window=[doc("source/a", "[recap] feat: index", repo="recap")])
        markdown = render_markdown(build(index, FakePC(memories=[memory("atomic-memory/m@1", "decision", "用 seekdb")])))
        assert "# 周报 · 2026-W44" in markdown
        assert "数据窗口 2026-10-20 → 2026-10-26" in markdown
        assert "- feat: index（recap）`source/a`" in markdown
        assert "- 决定：用 seekdb `atomic-memory/m@1`" in markdown
