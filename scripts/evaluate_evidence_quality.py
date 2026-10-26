# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline SciFact quality evaluation; never loaded by the product runtime."""

import argparse
import asyncio
import collections
import json
import math
import pathlib
import re

from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryHit
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class LiteralPipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(kind="document", text=s.content, sources=(s,))
            for s in request.sources
            if isinstance(s, ContentSource)
        )


parser = argparse.ArgumentParser(
    description="Evaluate literal evidence selection with public SciFact annotations through real capture, flush and search. Labels are used only for input construction and scoring."
)
parser.add_argument(
    "--data", type=pathlib.Path, required=True, help="Directory containing corpus.jsonl and claims_<split>.jsonl"
)
parser.add_argument("--split", choices=("train", "dev"), default="dev")
parser.add_argument("--output", type=pathlib.Path, required=True)
args = parser.parse_args()


def words(s):
    return re.findall(r"[a-z]+|\d+", s.lower())


corpus = {str(d["doc_id"]): d for d in map(json.loads, (args.data / "corpus.jsonl").read_text().splitlines())}
corpus = {k: d for k, d in corpus.items() if len(" ".join(d["abstract"]).encode()) <= 8192}
terms = {k: set(words(d["title"] + " " + " ".join(d["abstract"]))) for k, d in corpus.items()}
df = collections.Counter(t for ts in terms.values() for t in ts)
claims = list(map(json.loads, (args.data / ("claims_" + args.split + ".jsonl")).read_text().splitlines()))


def render(gold):
    return (
        TRUST_POLICY
        + "\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        + json.dumps({"trust": "untrusted_history", "items": gold}, ensure_ascii=False, separators=(",", ":"))
        + "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
    )


async def run():  # noqa: C901 - Construct real scopes, measure both budgets, and aggregate the dataset.
    rows = []
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=LiteralPipeline()) as runtime:
        if runtime.scopes is None:
            raise RuntimeError("Scope service is required")  # noqa: TRY003
        for claim in claims:
            if not claim["evidence"]:
                continue
            q = set(words(claim["claim"]))
            ranked = sorted(
                corpus, key=lambda k: (-sum(math.log((len(corpus) + 1) / (df[t] + 1)) for t in q & terms[k]), k)
            )
            target = next(iter(claim["evidence"]))
            if target not in corpus:
                continue
            ids = [target, *[k for k in ranked if k not in claim["evidence"]][:2]]
            ids.sort(key=lambda k: ranked.index(k))
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Scientific evidence",
                    summary="Offline quality evaluation",
                    idempotency_key="quality-" + str(claim["id"]),
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            texts = {key: " ".join(corpus[key]["abstract"]) for key in ids}
            for key in ids:
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(source_id="document-" + key, content=texts[key], metadata={"document_id": key})
                )
            while (await memory.flush()).processed:
                pass
            page = await memory.list()
            if page.memory_ref is None:
                raise RuntimeError("The written memory has no head")  # noqa: TRY003
            by_text = {value: key for key, value in texts.items()}
            hits = tuple(
                MemoryHit(
                    memory_ref=page.memory_ref,
                    entry_id=record.entry.entry_id,
                    entry_version_id=record.entry.entry_version_id,
                    text=record.entry.text,
                    score=1.0,
                    matched_by=("fts",),
                )
                for record in page.entries
            )
            ids_by_entry = {hit.entry_id: by_text[hit.text] for hit in hits}
            hit = next(hit for hit in hits if ids_by_entry[hit.entry_id] == target)
            citation = {
                "scope_id": scope.scope_id,
                "memory_ref": page.memory_ref.model_dump(mode="json"),
                "entry_id": hit.entry_id,
                "entry_version_id": hit.entry_version_id,
            }
            indices = sorted({i for e in claim["evidence"][target] for i in e["sentences"]})
            spans = []
            offset = 0
            for i, sentence in enumerate(corpus[target]["abstract"]):
                if i in indices:
                    if spans and spans[-1][1] + 1 == offset:
                        spans[-1] = (spans[-1][0], offset + len(sentence))
                    else:
                        spans.append((offset, offset + len(sentence)))
                offset += len(sentence) + 1
            expected = collections.Counter(words(" ".join(corpus[target]["abstract"][i] for i in indices)))
            gold = [{"citation": citation, "content": texts[target][a:b], "start": a, "end": b} for a, b in spans]
            search = await memory.search(SearchMemoryRequest(query=claim["claim"], mode="fts", limit=16))
            hits = search.hits
            for budget in (1400, 1800):
                result = PreparedContextBuilder().build_scopes_result(
                    request=PrepareContextRequest(query=claim["claim"], max_bytes=budget, evidence_selection=True),
                    current_scope_id=scope.scope_id,
                    memory_candidates=(
                        PreparedMemoryCandidates(scope_id=scope.scope_id, memory_ref=page.memory_ref, hits=hits),
                    ),
                )
                covered = {
                    i
                    for f in result.fragments
                    if ids_by_entry[f.address.entry_id] == target
                    for i in range(f.start, f.end)
                }
                actual = collections.Counter(
                    words("".join(texts[target][i] if i in covered else " " for i in range(len(texts[target]))))
                )
                rows.append({
                    "id": claim["id"],
                    "budget": budget,
                    "feasible": len(render(gold).encode()) <= budget,
                    "recall": sum((actual & expected).values()) / sum(expected.values()),
                    "full": all(i in covered for a, b in spans for i in range(a, b)),
                    "candidate_count": len(hits),
                    "target_recalled": any(ids_by_entry[h.entry_id] == target for h in hits),
                    "bytes": result.context.content_bytes,
                })
            if len(rows) % 80 == 0:
                print("processed", len(rows), flush=True)
    for feasible in (False, True):
        selected = [r for r in rows if not feasible or r["feasible"]]
        print(
            json.dumps({
                "feasible_only": feasible,
                "requests": len(selected),
                "recall": sum(r["recall"] for r in selected) / len(selected),
                "full": sum(r["full"] for r in selected) / len(selected),
            }),
            flush=True,
        )
    args.output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(run())
