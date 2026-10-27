"""seekdb as Recap's retrieval layer.

PowerContext answers "what is the current state". It cannot be asked "what did we
say about this same blocker six weeks ago" across the whole archive at scale, so
every durable item is mirrored into one seekdb collection and searched with a
single hybrid query (vector + full-text + scalar filters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import pyseekdb

TEXT_RE = re.compile(r"^[\w\s\u4e00-\u9fff.,:()\/\[\]#*+-]{0,200}$")

def sql_text(value: str) -> str:
    """Quote a full-text phrase only if it is plainly text: the whitelist excludes
    quotes, backslashes and `;`, so a refused value is the only way to break out."""
    if not TEXT_RE.match(value):
        raise ValueError(f"refused as unsafe full-text input: {value[:40]!r}")
    return f"'{value}'"


def sql_date(value: date | str) -> str:
    """A calendar-valid ISO date, quoted. Shape alone is not enough: `2026-13-45`
    matches the pattern but is not a day, and the comparison would silently never match."""
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"not an ISO date: {value!r}") from None
    return f"'{parsed.isoformat()}'"


def sql_vector(vector: list[float]) -> str:
    return "'[" + ",".join(repr(float(x)) for x in vector) + "]'"


@dataclass
class Doc:
    id: str
    text: str
    family: str
    kind: str
    ref: str
    created_at: date
    title: str = ""
    repo: str = ""
    scope_id: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "kind": self.kind,
            "ref": self.ref,
            "created_at": self.created_at.isoformat(),
            # seekdb rejects string range comparisons inside JSON metadata, so the
            # time window filters on an integer day key instead.
            "created_key": int(self.created_at.strftime("%Y%m%d")),
            "title": self.title,
            "repo": self.repo,
            "scope_id": self.scope_id,
        }


class RecapIndex:
    """Embedded seekdb collection, opened from a local data directory."""

    def __init__(self, path: str, name: str = "recap_docs", database: str = "recap"):
        admin = pyseekdb.AdminClient(path=path)
        if database not in (admin.list_databases() or []):
            admin.create_database(database)
        self.client = pyseekdb.Client(path=path, database=database)
        self.collection = self.client.get_or_create_collection(
            name=name, embedding_function=pyseekdb.DefaultEmbeddingFunction()
        )

    def upsert(self, docs: list[Doc]) -> int:
        if not docs:
            return 0
        self.collection.upsert(
            ids=[d.id for d in docs],
            documents=[d.text for d in docs],
            metadatas=[d.metadata() for d in docs],
        )
        return len(docs)

    def count(self) -> int:
        return self.collection.count()

    def delete_mirrored(self) -> int:
        """The mirror is disposable. Drop it before rebuilding so entries retired in
        PowerContext — forgotten memories, superseded notes — cannot resurface.
        Archived weekly reports are Recap's own output, not a mirror, and stay."""
        ids = self.collection.get(where={"family": {"$ne": "report"}}, include=[]).get("ids") or []
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def search(
        self,
        text: str,
        limit: int = 8,
        before: date | None = None,
        after: date | None = None,
        families: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if families:
            where["family"] = {"$in": families}
        window: dict[str, Any] = {}
        if before:
            window["$lt"] = int(before.strftime("%Y%m%d"))
        if after:
            window["$gte"] = int(after.strftime("%Y%m%d"))
        if window:
            where["created_key"] = window

        res = self.collection.hybrid_search(
            query={"where_document": {"$contains": text}, "where": where, "n_results": limit * 3},
            knn={"query_texts": [text], "where": where, "n_results": limit * 3},
            rank={"rrf": {"rank_window_size": limit * 6, "rank_constant": 60}},
            n_results=limit,
            include=["metadatas", "documents", "distances"],
        )
        out: list[dict[str, Any]] = []
        ids = res.get("ids") or [[]]
        metas = res.get("metadatas") or [[]]
        docs = res.get("documents") or [[]]
        dists = res.get("distances") or [[]]
        for i, doc_id in enumerate(ids[0]):
            out.append(
                {
                    "id": doc_id,
                    "text": docs[0][i],
                    "metadata": metas[0][i],
                    "distance": dists[0][i] if i < len(dists[0]) else None,
                    "ref": (metas[0][i] or {}).get("ref", doc_id),
                }
            )
        return out

    def table(self) -> str:
        """The physical table behind a collection is `c$v2$<uuid>`; resolve it once."""
        if not hasattr(self, "_table"):
            with self._cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name LIKE 'c$v2$%%'"
                )
                rows = list(cur.fetchall())
            if not rows:
                raise RuntimeError("collection table not found; run `recap index` first")
            first = rows[0]
            self._table = first[0] if isinstance(first, (tuple, list)) else first["table_name"]
        return self._table

    def describe(self) -> str:
        with self._cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{self.table()}`")
            cols = [r[0] if isinstance(r, (tuple, list)) else r["Field"] for r in cur.fetchall()]
        return f"table={self.table()} columns={cols}"

    def raw_hybrid_sql(self, phrase: str, vector: list[float], cutoff: date, limit: int = 5) -> tuple[str, list]:
        """One statement: full-text + scalar window on JSON metadata + vector ordering.

        pylibseekdb's cursor accepts no bind parameters, so inputs go through the
        whitelist escapers below rather than string interpolation.
        """
        table = self.table()
        sql = (
            f"SELECT _id, JSON_VALUE(metadata, '$.ref') AS ref, "
            f"MATCH(document) AGAINST({sql_text(phrase)}) AS ft_hit, "
            f"L2_DISTANCE(embedding, {sql_vector(vector)}) AS vec_dist "
            f"FROM `{table}` "
            f"WHERE MATCH(document) AGAINST({sql_text(phrase)}) "
            f"AND JSON_VALUE(metadata, '$.created_at') < {sql_date(cutoff)} "
            f"ORDER BY vec_dist APPROXIMATE LIMIT {int(limit)}"
        )
        with self._cursor() as cur:
            cur.execute(sql)
            rows = list(cur.fetchall())
        return sql, rows

    def _cursor(self):
        """pylibseekdb's Cursor is not a context manager; wrap it."""
        index = self

        class _Cursor:
            def __enter__(self_inner):
                self_inner.cur = index.collection.client.get_raw_connection().cursor()
                return self_inner.cur

            def __exit__(self_inner, *exc):
                self_inner.cur.close()
                return False

        return _Cursor()

    def embed(self, text: str) -> list[float]:
        return self.collection.embedding_function([text])[0]
