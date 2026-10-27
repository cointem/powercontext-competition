"""`recap` command line.

  recap doctor                     check PowerContext server + seekdb wiring
  recap collect --since --until    write real work traces into PowerContext
  recap index                      mirror PowerContext artifacts into seekdb
  recap report --since --until     compose the weekly report (optionally archive it)
  recap search "查询" [--before]   hybrid search over the archive
  recap snapshot                   write the acceptance sample (report + real queries)
  recap seed                       export web/seed.json for the hosted board
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from recap import __version__
from recap.config import Config, load
from recap.indexer import refresh
from recap.pctx import PowerContext
from recap.report import compose, render_markdown
from recap.store import Doc, RecapIndex
from recap.ingest import week_label


def _open(cfg: Config) -> tuple[PowerContext, RecapIndex]:
    return PowerContext(cfg), RecapIndex(path=str(cfg.seekdb_path))


def _state(cfg: Config) -> dict:
    if cfg.state_path.exists():
        return json.loads(cfg.state_path.read_text())
    return {}


def _save_state(cfg: Config, data: dict) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _window(args) -> tuple[date, date, str]:
    until = getattr(args, "until", None) or date.today()
    since = getattr(args, "since", None) or (until - timedelta(days=6))
    return since, until, week_label(until)


def cmd_doctor(cfg: Config, args) -> int:
    async def run() -> dict:
        pc = PowerContext(cfg)
        scope_id = await pc.ensure_scope()
        info = {
            "powercontext": cfg.server_url,
            "scope_id": scope_id,
            "memories": len(await pc.active_memories(scope_id)),
            "sources": len(await pc.journal(scope_id)),
        }
        return info

    info = asyncio.run(run())
    index = RecapIndex(path=str(cfg.seekdb_path))
    info["seekdb"] = f"{cfg.seekdb_path} docs={index.count()} {index.describe()}"
    print(json.dumps(info, ensure_ascii=False, indent=2))
    _save_state(cfg, _state(cfg) | {"scope_id": info["scope_id"]})
    return 0


def cmd_collect(cfg: Config, args) -> int:
    from recap.ingest import collect

    since, until, week = _window(args)
    state = _state(cfg)

    async def run() -> dict:
        pc = PowerContext(cfg)
        scope_id = state.get("scope_id") or await pc.ensure_scope()
        result = await collect(cfg, pc, scope_id, since, until)
        return {
            "week": result.week,
            "sources": result.sources,
            "memories": len(result.memories),
            "repos": {b.repo: len(b.commits) for b in result.bundles},
            "handoffs": [b.handoff_ref for b in result.bundles if b.handoff_ref],
        }

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
    return 0


def cmd_index(cfg: Config, args) -> int:
    since, until, _ = _window(args)
    state = _state(cfg)

    async def run() -> tuple[str, int]:
        pc = PowerContext(cfg)
        scope_id = state.get("scope_id") or await pc.ensure_scope()
        index = RecapIndex(path=str(cfg.seekdb_path))
        return scope_id, await refresh(cfg, pc, scope_id, index, fallback=until)

    scope_id, (dropped, docs) = asyncio.run(run())
    print(
        json.dumps(
            {"scope_id": scope_id, "dropped_docs": dropped, "mirrored_docs": docs},
            ensure_ascii=False,
        )
    )
    return 0


def cmd_report(cfg: Config, args) -> int:
    since, until, week = _window(args)
    state = _state(cfg)

    async def run() -> dict:
        pc = PowerContext(cfg)
        scope_id = state.get("scope_id") or await pc.ensure_scope()
        index = RecapIndex(path=str(cfg.seekdb_path))
        await refresh(cfg, pc, scope_id, index, fallback=until)
        return await compose(scope_id, pc, index, since, until, week)

    report = asyncio.run(run())
    markdown = render_markdown(report)
    out = Path(args.out) if getattr(args, "out", None) else cfg.data_dir / f"report-{week}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown)
    print(markdown)
    print(f"\n[saved {out}]")

    if getattr(args, "archive", False):

        async def archive() -> str:
            pc = PowerContext(cfg)
            scope_id = state.get("scope_id") or await pc.ensure_scope()
            source_id = await pc.capture(scope_id, f"report:{week}", markdown, {"kind": "weekly-report"})
            index = RecapIndex(path=str(cfg.seekdb_path))
            index.upsert(
                [
                    Doc(
                        id=f"report:{week}",
                        text=markdown,
                        family="report",
                        kind="weekly_report",
                        ref=f"report/{week}",
                        created_at=until,
                        title=f"周报 {week}",
                        scope_id=scope_id,
                    )
                ]
            )
            return source_id

        print(f"[archived as Source {asyncio.run(archive())}] — 下一周生成时会把它当历史语料检索到")
    return 0


def cmd_search(cfg: Config, args) -> int:
    index = RecapIndex(path=str(cfg.seekdb_path))
    hits = index.search(
        args.text,
        limit=args.limit,
        before=date.fromisoformat(args.before) if args.before else None,
        after=date.fromisoformat(args.after) if args.after else None,
        families=args.family or None,
    )
    for hit in hits:
        meta = hit["metadata"] or {}
        print(f"- {meta.get('ref', hit['id'])} [{meta.get('family')}·{meta.get('created_at')}] {hit['text'][:120]}")
    if args.raw_sql:
        cutoff = date.fromisoformat(args.before) if args.before else date.today()
        sql, rows = index.raw_hybrid_sql(args.text, index.embed(args.text), cutoff, limit=args.limit)
        print("\n-- one statement, three predicates --")
        print(sql[:220] + " …")
        print(f"rows={len(rows)}")
    return 0


def cmd_snapshot(cfg: Config, args) -> int:
    from recap.web_export import export

    since, until, week = _window(args)
    state = _state(cfg)

    async def scope() -> str:
        pc = PowerContext(cfg)
        return state.get("scope_id") or await pc.ensure_scope()

    scope_id = asyncio.run(scope())
    out = Path(args.out) if args.out else cfg.data_dir / "snapshot.json"
    print(f"[wrote {export(cfg, scope_id, since, until, week, out)}]")
    return 0


def cmd_seed(cfg: Config, args) -> int:
    from recap.web_export import export_seed

    until = getattr(args, "until", None) or date.today()
    state = _state(cfg)

    async def scope() -> str:
        pc = PowerContext(cfg)
        return state.get("scope_id") or await pc.ensure_scope()

    scope_id = asyncio.run(scope())
    out = Path(args.out) if args.out else cfg.web_dir / "seed.json"
    print(f"[wrote {export_seed(cfg, scope_id, out, until)}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recap", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("doctor", cmd_doctor), ("collect", cmd_collect), ("index", cmd_index), ("report", cmd_report)):
        sp = sub.add_parser(name, help=name)
        sp.add_argument("--since", type=date.fromisoformat)
        sp.add_argument("--until", type=date.fromisoformat)
        sp.add_argument("--out")
        sp.add_argument("--archive", action="store_true")
        sp.set_defaults(handler=handler)

    ss = sub.add_parser("search", help="hybrid search over the archive")
    ss.add_argument("text")
    ss.add_argument("--limit", type=int, default=8)
    ss.add_argument("--before", type=date.fromisoformat)
    ss.add_argument("--after", type=date.fromisoformat)
    ss.add_argument("--family", action="append")
    ss.add_argument("--raw-sql", action="store_true")
    ss.set_defaults(handler=cmd_search)

    sn = sub.add_parser("snapshot", help="export the run's real payload as data/snapshot.json")
    sn.add_argument("--since", type=date.fromisoformat)
    sn.add_argument("--until", type=date.fromisoformat)
    sn.add_argument("--out")
    sn.set_defaults(handler=cmd_snapshot)

    sd = sub.add_parser("seed", help="export web/seed.json for the hosted board")
    sd.add_argument("--until", type=date.fromisoformat)
    sd.add_argument("--out")
    sd.set_defaults(handler=cmd_seed)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = load(Path(args.config) if args.config else None)
    return args.handler(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
