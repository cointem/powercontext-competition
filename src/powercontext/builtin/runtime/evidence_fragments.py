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

"""Query-weighted literal sentence selection with complete wire-budget accounting."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.models import PreparedContext
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

if TYPE_CHECKING:
    from powercontext.builtin.runtime.prepared_context import MemoryEntryAddress

_STOP = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "from",
    "by",
    "with",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "as",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "their",
    "we",
    "our",
    "they",
    "can",
    "could",
    "would",
    "should",
    "have",
    "has",
    "had",
}


def _terms(text):
    terms = re.findall(r"[^\W_]+", text.lower())
    return [term[:-1] if len(term) > 4 and term.endswith("s") else term for term in terms if term not in _STOP]


def _spans(text):
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        end = boundary.start()
        if text[start:end].strip():
            yield start, end
        start = boundary.end()
    if text[start:].strip():
        yield start, len(text)


@dataclass(frozen=True)
class EvidenceFragment:
    address: MemoryEntryAddress
    start: int
    end: int
    text: str


def _rank_sentences(query_text, groups):
    from powercontext.builtin.runtime.prepared_context import MemoryEntryAddress

    candidates = []
    document_terms = []
    query = set(_terms(query_text))
    for group in groups:
        if group.memory_ref is None:
            continue
        for hit in group.hits:
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=group.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            terms = Counter(_terms(hit.text))
            document_terms.append(set(terms))
            for start, end in _spans(hit.text):
                candidates.append((address, hit.text, start, end, Counter(_terms(hit.text[start:end]))))
    df = Counter(term for terms in document_terms for term in terms)
    weights = {term: 1 + math.log((len(document_terms) + 1) / (df[term] + 1)) for term in query}
    ranked = []
    for index, (address, text, start, end, terms) in enumerate(candidates):
        overlap = query & terms.keys()
        if not overlap:
            continue
        score = sum(weights[term] * (1 + math.log(terms[term])) for term in overlap)
        score *= 1 + len(overlap) / max(len(query), 1)
        score /= max(1, len(text[start:end].encode()) / 300) ** 0.25
        ranked.append((-score, index, address, text, start, end))

    return ranked


def build_evidence_context(request, groups):
    from powercontext.builtin.runtime.prepared_context import PreparedContextBuild

    ranked = _rank_sentences(request.query, groups)

    def render(fragments):
        items = [
            {
                "citation": {
                    "scope_id": fragment.address.memory.scope_id,
                    "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                    "entry_id": fragment.address.entry_id,
                    "entry_version_id": fragment.address.entry_version_id,
                },
                "content": fragment.text,
                "start": fragment.start,
                "end": fragment.end,
            }
            for fragment in fragments
        ]
        payload = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
        return (
            f"{TRUST_POLICY}\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{payload}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
        )

    selected = []
    seen = set()
    for _, _, address, text, start, end in sorted(ranked, key=lambda item: item[:2]):
        identity = (address.memory.scope_id, address.entry_id, address.entry_version_id, text[start:end])
        if identity in seen:
            continue
        fragment = EvidenceFragment(address, start, end, text[start:end])
        proposal = [*selected, fragment]
        # Adjacent sentences from the same entry share one citation and preserve intervening whitespace.
        for index, prior in enumerate(selected):
            if prior.address == address and (
                text[prior.end : start].isspace()
                if prior.end <= start
                else text[end : prior.start].isspace()
                if end <= prior.start
                else False
            ):
                left, right = min(prior.start, start), max(prior.end, end)
                proposal = list(selected)
                proposal[index] = EvidenceFragment(address, left, right, text[left:right])
                break
        if len(render(proposal).encode()) <= request.max_bytes:
            selected = proposal
            seen.add(identity)
    if not selected:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = render(selected)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode())),
        origins=tuple(fragment.address for fragment in selected),
        fragments=tuple(selected),
    )
