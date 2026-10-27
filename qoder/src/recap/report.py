"""Compose a weekly report out of working context.

Deliberately deterministic: every bullet is copied from a PowerContext artifact
or a seekdb hit and carries the citation it came from. A report line with no
reference is a bug, not a style choice.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from recap.indexer import KIND_LABEL
from recap.pctx import PowerContext
from recap.store import RecapIndex

MEMORY_ORDER = ["decision", "constraint", "state", "next_step", "fact"]
# Only human-readable work traces are progress; the JSON envelopes the journal
# keeps for contracts, boundaries and outcomes are process records, not bullets.
PROGRESS_KINDS = {"evidence", "note"}


async def compose(
    scope_id: str,
    pc: PowerContext,
    index: RecapIndex,
    since: date,
    until: date,
    week: str,
) -> dict[str, Any]:
    memories = await pc.active_memories(scope_id)
    handoffs = await pc.handoffs(scope_id)

    in_window = [
        doc
        for doc in index.search("本周 提交 commit 进展", limit=80, after=since)
        if _in_window(doc, since, until) and (doc["metadata"] or {}).get("kind") in PROGRESS_KINDS
    ]
    progress = _deduped([_bullet(doc) for doc in in_window])[:14]

    carry_over = _carry_over(index, since, in_window)
    open_items = _deduped(_open_questions(handoffs, memories))[:8]
    next_week = _deduped(_next_actions(handoffs, memories))[:8]

    decisions = [
        {"text": mem["text"], "cite": mem["ref"], "label": KIND_LABEL.get(mem["kind"], mem["kind"])}
        for mem in _deduped(
            sorted(
                (m for m in memories if m["kind"] in {"decision", "constraint", "state"}),
                key=lambda m: MEMORY_ORDER.index(m["kind"]),
            )
        )
    ]

    return {
        "week": week,
        "scope_id": scope_id,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "summary": {
            "progress": len(progress),
            "active_memories": len(_deduped(memories)),
            "handoffs": len(handoffs),
            "carry_over": len(carry_over),
        },
        "progress": progress,
        "decisions": decisions,
        "open_items": open_items,
        "next_week": next_week,
        "carry_over": carry_over,
        "refs": sorted(
            {item["cite"] for item in progress}
            | {item["cite"] for item in decisions}
            | {item["cite"] for item in open_items}
            | {item["cite"] for item in next_week}
            | {item["ref"] for item in carry_over}
        ),
    }


def _deduped(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Same text re-remembered across runs keeps its first (lowest-revision) citation."""
    seen: dict[str, dict[str, str]] = {}
    for item in items:
        seen.setdefault(item["text"], item)
    return list(seen.values())


def _in_window(doc: dict[str, Any], since: date, until: date) -> bool:
    key = (doc.get("metadata") or {}).get("created_key")
    if key is None:
        return False
    return int(since.strftime("%Y%m%d")) <= int(key) <= int(until.strftime("%Y%m%d"))


def _plain(line: str) -> str:
    """A note's first line is a markdown heading; a report line is a bullet."""
    return re.sub(r"^#+\s*", "", line.strip())


def _bullet(doc: dict[str, Any]) -> dict[str, str]:
    meta = doc.get("metadata") or {}
    first_line = (doc["text"] or "").splitlines()[0] if doc.get("text") else ""
    repo = meta.get("repo") or ""
    text = _plain(meta.get("title") or first_line)[:200]
    # Sources carry an "[repo] " prefix so the journal reads standalone; in the
    # report the repo already appears as the suffix, so it is not shown twice.
    if repo and text.startswith(f"[{repo}]"):
        text = text[len(repo) + 2 :].strip()
    return {"text": text, "cite": meta.get("ref", doc["id"]), "repo": repo}


def _carry_over(index: RecapIndex, since: date, window_docs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Earlier records about this week's topics — the archive lookup PowerContext alone won't do."""
    seen: dict[str, dict[str, str]] = {}
    for doc in window_docs[:4]:
        topic = (doc.get("title") or doc["text"].splitlines()[0])[:80]
        for hit in index.search(topic, limit=4, before=since):
            meta = hit.get("metadata") or {}
            ref = meta.get("ref") or hit["id"]
            if ref in seen:
                continue
            seen[ref] = {"text": _plain(meta.get("title") or hit["text"].splitlines()[0])[:180], "ref": ref}
    return list(seen.values())[:6]


def _open_questions(handoffs: list[dict], memories: list[dict]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for handoff in handoffs:
        content = handoff.get("content") or {}
        for omission in content.get("omissions") or []:
            text = omission.get("text") if isinstance(omission, dict) else str(omission)
            out.append({"text": str(text), "cite": handoff["ref"]})
        if content.get("disposition") == "blocked":
            out.append({"text": f"阻塞中：{content.get('objective', '')}", "cite": handoff["ref"]})
    for mem in memories:
        if mem["kind"] == "state":
            out.append({"text": mem["text"], "cite": mem["ref"]})
    return out[:8]


def _next_actions(handoffs: list[dict], memories: list[dict]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for handoff in handoffs:
        content = handoff.get("content") or {}
        action = content.get("next_action")
        if isinstance(action, dict) and action.get("text"):
            out.append({"text": action["text"], "cite": handoff["ref"]})
    for mem in memories:
        if mem["kind"] == "next_step":
            out.append({"text": mem["text"], "cite": mem["ref"]})
    return out


def render_markdown(report: dict[str, Any]) -> str:
    window = report["window"]
    lines = [
        f"# 周报 · {report['week']}",
        "",
        f"数据窗口 {window['since']} → {window['until']}，Scope `{report['scope_id']}`。",
        "每条结论后的引用可回溯到 PowerContext artifact 或 Source。",
        "",
        "## 本周进展",
    ]
    lines += [
        (f"- {item['text']}（{item['repo']}）`{item['cite']}`" if item["repo"] else f"- {item['text']} `{item['cite']}`")
        for item in report["progress"]
    ] or ["- 窗口内没有可引用的工作痕迹。"]

    lines += ["", "## 关键决定与约束"]
    lines += [f"- {item['label']}：{item['text']} `{item['cite']}`" for item in report["decisions"]] or ["- 无。"]

    lines += ["", "## 未决与阻塞"]
    lines += [f"- {item['text']} `{item['cite']}`" for item in report["open_items"]] or ["- 无。"]

    lines += ["", "## 下周计划"]
    lines += [f"- {item['text']} `{item['cite']}`" for item in report["next_week"]] or ["- 无。"]

    lines += ["", "## 历史回溯（seekdb 混合检索，窗口之前）"]
    lines += [f"- {item['text']} `{item['ref']}`" for item in report["carry_over"]] or ["- 无相关历史记录。"]

    lines += ["", "## 引用清单"]
    lines += [f"- `{ref}`" for ref in report["refs"]]
    summary = report["summary"]
    lines += [
        "",
        "---",
        (
            f"统计：进展 {summary['progress']} 条，活跃 Memory {summary['active_memories']} 条，"
            f"Handoff {summary['handoffs']} 份，历史回溯 {summary['carry_over']} 条。"
        ),
    ]
    return "\n".join(lines)
