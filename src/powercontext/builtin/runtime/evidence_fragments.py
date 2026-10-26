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
        if "\n" not in boundary.group() and re.search(
            r"\b(?:e\.g|i\.e|et al|vs|fig|dr|mr|mrs|prof|approx|ref)\.$", text[:end], re.I
        ):
            continue
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


def _key(address):
    ref = address.memory.artifact
    return (
        address.memory.scope_id,
        ref.family,
        ref.artifact_id,
        ref.revision,
        address.entry_id,
        address.entry_version_id,
    )


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
        # Scientific prose often alternates derived forms (activate/activation).
        # Prefix matches are weaker than complete words and never change the text.
        prefixes = {term[:5] for term in terms if len(term) >= 6}
        related = {term for term in query - overlap if len(term) >= 6 and term[:5] in prefixes}
        if not overlap and not related:
            continue
        score = sum(weights[term] * (1 + math.log(terms[term])) for term in overlap)
        score += sum(0.5 * weights[term] for term in related)
        score *= 1 + (len(overlap) + 0.5 * len(related)) / max(len(query), 1)
        score /= max(1, len(text[start:end].encode()) / 300) ** 0.25
        sentence = text[start:end].lower()
        if re.match(r"\s*(?:background|objective|purpose|aim)\b", sentence):
            score *= 0.65
        if re.search(r"\b(?:results?|conclusions?|findings|found|showed|demonstrated|revealed)\b", sentence):
            score *= 1.25
        score *= 1 + 0.25 * start / max(1, len(text))
        ranked.append((-score, index, address, text, start, end))

    return ranked


def _render_fragments(fragments):
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
    return f"{TRUST_POLICY}\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{payload}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


def _merge_fragments(ordered, indices):
    intervals, sources = {}, {}
    for index in sorted(indices):
        _, _, address, text, start, end = ordered[index]
        key = _key(address)
        intervals.setdefault(key, []).append((start, end))
        sources[key] = (address, text)
    result = []
    for key, ranges in intervals.items():
        merged = []
        address, text = sources[key]
        for start, end in sorted(ranges):
            if merged and (start <= merged[-1][1] or text[merged[-1][1] : start].isspace()):
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result.extend(EvidenceFragment(address, start, end, text[start:end]) for start, end in merged)
    return result


def _complete_selection(seed, ordered, max_bytes):
    chosen = {seed}
    current = _merge_fragments(ordered, chosen)
    size = len(_render_fragments(current).encode())
    while True:
        additions = []
        for index, item in enumerate(ordered):
            if index in chosen:
                continue
            proposal = _merge_fragments(ordered, chosen | {index})
            proposed_size = len(_render_fragments(proposal).encode())
            if proposed_size <= max_bytes:
                additions.append((-item[0] / max(1, proposed_size - size) ** 0.5, -index, proposal, proposed_size))
        if not additions:
            break
        _, negative_index, current, size = max(additions, key=lambda item: item[:2])
        chosen.add(-negative_index)
    return sum(-ordered[index][0] for index in chosen), current


def build_evidence_context(request, groups):
    from powercontext.builtin.runtime.prepared_context import PreparedContextBuild

    # Explore several starting sentences. Opening a new citation is costly, whereas
    # extending an existing source interval can use most of the budget for evidence.
    unique = {}
    for item in sorted(_rank_sentences(request.query, groups), key=lambda item: item[:2]):
        unique.setdefault((_key(item[2]), item[3][item[4] : item[5]]), item)
    ordered = [
        item
        for item in unique.values()
        if len(_render_fragments([EvidenceFragment(item[2], item[4], item[5], item[3][item[4] : item[5]])]).encode())
        <= request.max_bytes
    ][:64]
    best_score, selected = 0.0, []
    for seed in range(min(12, len(ordered))):
        score, current = _complete_selection(seed, ordered, request.max_bytes)
        if score > best_score:
            best_score, selected = score, current
    if not selected:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = _render_fragments(selected)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode())),
        origins=tuple(fragment.address for fragment in selected),
        fragments=tuple(selected),
    )
