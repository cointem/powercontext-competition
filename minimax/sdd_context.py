"""CLI for real seekdb retrieval and restartable PowerContext task state."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import pymysql

POWERCONTEXT_COMMIT = "dab240063bfcfa7917898dc44da4baadb3747bd0"


def digest(content):
    return hashlib.sha256(content).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class Materials:
    def __init__(self):
        database = os.environ.get("SEEKDB_DATABASE", "minimax_sdd")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", database):
            raise ValueError("Invalid SEEKDB_DATABASE")
        self.connection = pymysql.connect(
            host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
            port=int(os.environ.get("SEEKDB_PORT", "12883")),
            user=os.environ.get("SEEKDB_USER", "root"),
            password=os.environ.get("SEEKDB_PASSWORD", ""),
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with self.connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cursor.execute(f"USE `{database}`")
            cursor.execute("""CREATE TABLE IF NOT EXISTS sdd_materials (
                project_id VARCHAR(128) NOT NULL,
                path VARCHAR(256) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                sha256 CHAR(64) NOT NULL,
                body LONGTEXT NOT NULL,
                PRIMARY KEY (project_id, path)
            )""")
        self.connection.commit()

    def close(self):
        self.connection.close()

    def ingest(self, root, project_id):
        """Atomically replace this project's index, also removing deleted sources."""
        root = Path(root).resolve()
        rows = []
        for relative, kind in [("orders.py", "code"), ("data/orders.json", "data")]:
            path = root / relative
            body = path.read_text()
            rows.append((project_id, relative, kind, digest(body.encode()), body))
        for folder, pattern, kind in [
            ("specs", "*.md", "spec"),
            ("tests", "*.py", "test"),
        ]:
            for path in sorted((root / folder).glob(pattern)):
                body = path.read_text()
                rows.append(
                    (
                        project_id,
                        path.relative_to(root).as_posix(),
                        kind,
                        digest(body.encode()),
                        body,
                    )
                )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM sdd_materials WHERE project_id=%s", (project_id,)
                )
                cursor.executemany(
                    "INSERT INTO sdd_materials VALUES (%s,%s,%s,%s,%s)", rows
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "project_id": project_id,
            "documents": [dict(path=r[1], kind=r[2], sha256=r[3]) for r in rows],
        }

    def retrieve(self, project_id, query, root):
        terms = list(
            dict.fromkeys(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]{2,}", query.lower()))
        )[:24]
        if not terms:
            raise ValueError("Query needs at least one English identifier or word")
        # LOCATE treats underscores and percent signs literally, unlike SQL LIKE.
        score = " + ".join(
            ["IF(LOCATE(%s, LOWER(CONCAT(path, ' ', body))) > 0, 1, 0)"] * len(terms)
        )
        sql = f"SELECT path,kind,sha256,body,({score}) AS relevance FROM sdd_materials WHERE project_id=%s HAVING relevance>0 ORDER BY relevance DESC,path LIMIT 30"
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            version = cursor.fetchone()["version"]
            cursor.execute(sql, (*terms, project_id))
            rows = list(cursor.fetchall())
        root = Path(root).resolve()
        for row in rows:
            source = (root / row["path"]).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError(f"Missing source: {row['path']}")
            if digest(source.read_bytes()) != row["sha256"]:
                raise ValueError(f"Stale source; re-import: {row['path']}")
            row["line_start"] = 1
            row["line_end"] = len(row["body"].splitlines())
        return {
            "backend": "seekdb",
            "server_version": version,
            "sql": sql,
            "parameters": [*terms, project_id],
            "sources": rows,
        }


async def memory_operation(directory, mode, state=None):
    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import (
        BuiltinConfig,
        RememberMemoryRequest,
        SearchMemoryRequest,
        open_builtin_runtime,
    )
    from powercontext.builtin.scope import ScopeDraft

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pointer = directory / "scope.json"
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}")
    )
    async with open_builtin_runtime(
        config, scheduler_path=directory / "scheduler.db"
    ) as runtime:
        if not pointer.exists():
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="SDD order query development",
                    summary="Persist order filter progress and validation across CLI restarts.",
                    idempotency_key="minimax-sdd-state",
                )
            )
            write_json(pointer, {"scope_id": scope.scope_id})
        scope_id = json.loads(pointer.read_text())["scope_id"]
        memory = runtime.memory.for_scope(scope_id)
        if mode == "save":
            saved = await memory.remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="outcome",
                            text="sddtaskstate "
                            + json.dumps(state, ensure_ascii=False),
                        ),
                    )
                )
            )
            return {
                "backend": "PowerContext",
                "commit": POWERCONTEXT_COMMIT,
                "pid": os.getpid(),
                "scope_id": scope_id,
                "memory_ref": saved.memory_ref.model_dump(mode="json"),
                "saved_state": state,
            }
        result = await memory.search(
            SearchMemoryRequest(query="sddtaskstate", mode="fts", limit=50)
        )
        hits = [h for h in result.hits if h.text.startswith("sddtaskstate ")]
        latest = max(
            hits,
            key=lambda h: json.loads(h.text.split(" ", 1)[1])["sequence"],
            default=None,
        )
        return {
            "backend": "PowerContext",
            "commit": POWERCONTEXT_COMMIT,
            "pid": os.getpid(),
            "scope_id": scope_id,
            "state": json.loads(latest.text.split(" ", 1)[1]) if latest else None,
            "citation": latest.model_dump(mode="json") if latest else None,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import", "context", "save", "resume"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--project", default="orders-demo")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--query", default="orders list_orders status customer_id limit"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command in {"import", "context"}:
        if args.root is None:
            parser.error("--root is required")
        materials = Materials()
        try:
            if args.command == "import":
                result = materials.ingest(args.root, args.project)
            else:
                result = materials.retrieve(args.project, args.query, args.root)
                with redirect_stdout(sys.stderr):
                    result["task_state"] = asyncio.run(
                        memory_operation(args.state_dir, "resume")
                    )
                previous = result["task_state"]["state"]
                result["next_action"] = (
                    previous["todo"][0]
                    if previous and previous["todo"]
                    else "No pending action"
                )
        finally:
            materials.close()
    else:
        if args.command == "save" and args.state is None:
            parser.error("--state is required for save")
        state = json.loads(args.state.read_text()) if args.state else None
        with redirect_stdout(sys.stderr):
            result = asyncio.run(memory_operation(args.state_dir, args.command, state))
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
