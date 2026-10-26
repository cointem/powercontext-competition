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

"""Offline PDF quality evaluator; annotations are never passed to retrieval."""

import argparse
import asyncio
import collections
import json
import pathlib
import re
from types import SimpleNamespace as NS
from typing import Any

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document, search_pdf_pages

parser = argparse.ArgumentParser(
    description="Measure parsed text and ranked pages against public FinanceBench labels. Retrieval uses a lightweight public-list adapter; run official tests for persistence and citation validation."
)
parser.add_argument("--data", type=pathlib.Path, required=True, help="FinanceBench JSONL annotations")
parser.add_argument("--pdf-dir", type=pathlib.Path, required=True, help="PDFs named by document ID")
parser.add_argument("--output", type=pathlib.Path, required=True)
args = parser.parse_args()


def tokens(s):
    return collections.Counter(re.findall(r"[a-z]+|\d+", s.lower()))


pages = {}
for path in sorted(args.pdf_dir.glob("*.pdf")):
    result = parse_pdf_document(PDFDocumentInput(document_id=path.stem, content=path.read_bytes()))
    pages[path.stem] = [page.text for page in result]
    print("parsed", path.stem, len(result), flush=True)


class Memory:
    def __init__(self, docs):
        self.entries = tuple(
            NS(
                entry=NS(text=doc + "\n" + text, sources=(NS(source_type="pdf-page", source_id=doc + ":" + str(i)),)),
                citation=(doc, i),
            )
            for doc in docs
            for i, text in enumerate(pages[doc])
        )

    async def list(self):
        return NS(entries=self.entries)


async def run():
    records = [r for r in map(json.loads, args.data.read_text().splitlines()) if r["doc_name"] in pages]
    memory = Memory(pages)
    results: list[dict[str, Any]] = []
    for r in records:
        hits = await search_pdf_pages(memory, r["question"], limit=20)
        target = {(e["doc_name"], e["evidence_page_num"]) for e in r["evidence"]}
        evidence_recall = []
        page_f1 = []
        for e in r["evidence"]:
            actual = tokens(pages[e["doc_name"]][e["evidence_page_num"]])
            expected = tokens(e["evidence_text"])
            full = tokens(e["evidence_text_full_page"])
            evidence_recall.append(sum((actual & expected).values()) / sum(expected.values()))
            page_f1.append(2 * sum((actual & full).values()) / (sum(actual.values()) + sum(full.values())))
        results.append({
            "id": r["financebench_id"],
            "doc": r["doc_name"],
            "question": r["question"],
            "target": sorted(target),
            "hits": hits,
            "recall": len(set(hits) & target) / len(target),
            "text_recall": min(evidence_recall),
            "text_f1": min(page_f1),
        })
    for doc in pages:
        rows = [r for r in results if r["doc"] == doc]
        if not rows:
            continue
        targets = sum(len(row["target"]) for row in rows)
        found = sum(len(set(map(tuple, row["target"])) & set(map(tuple, row["hits"]))) for row in rows)
        print(
            json.dumps({
                "document": doc,
                "questions": len(rows),
                "evidence_pages": targets,
                "matched_pages": found,
                "recall_at_20": found / targets,
                "minimum_text_recall": min(row["text_recall"] for row in rows),
                "minimum_page_f1": min(row["text_f1"] for row in rows),
            }),
            flush=True,
        )
    args.output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(run())
