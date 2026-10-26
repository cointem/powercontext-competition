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
    terms = []
    for word in re.findall(r"[^\W_]+", text.lower()):
        for term in re.findall(r"[\u3400-\u9fff]+|[^\u3400-\u9fff]+", word):
            if "\u3400" <= term[0] <= "\u9fff":
                terms.extend(term[index : index + 2] for index in range(max(1, len(term) - 1)))
            elif term not in _STOP:
                terms.append(term[:-1] if len(term) > 4 and term.endswith("s") else term)
    return terms


def _spans(text):
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|(?<=[。！？])\s*|\n+", text):  # noqa: RUF001 - CJK punctuation
        end = boundary.start()
        if "\n" not in boundary.group() and re.search(
            r"\b(?:e\.g|i\.e|et al|vs|fig|dr|mr|mrs|prof|approx|ref)\.$", text[:end], re.I
        ):
            continue
        if text[start:end].strip():
            yield start, boundary.end()
        start = boundary.end()
    if text[start:].strip():
        yield start, len(text)


@dataclass(frozen=True)
class EvidenceFragment:
    address: MemoryEntryAddress
    start: int
    end: int
    text: str


def _small_spans(text, start, end, limit):
    """Split an oversized sentence on original word boundaries, then characters."""
    used, last_space = 0, None
    for index in range(start, end):
        char = text[index]
        cost = len(json.dumps(char, ensure_ascii=False).encode()) - 2
        if used + cost > limit and index > start:
            split = last_space if last_space is not None and last_space > start else index
            yield start, split
            start = split
            used = len(json.dumps(text[start:index], ensure_ascii=False).encode()) - 2
            last_space = None
        used += cost
        if char.isspace():
            last_space = index + 1
    if start < end:
        yield start, end


def _bounded_spans(text, address, max_bytes):
    if max_bytes is None:
        yield from _spans(text)
        return
    # Use the longest offsets for a conservative citation/envelope allowance.
    overhead = len(_render_fragments([EvidenceFragment(address, len(text), len(text), "")]).encode())
    available = max_bytes - overhead
    if available <= 0:
        return
    for start, end in _spans(text):
        size = len(json.dumps(text[start:end], ensure_ascii=False).encode()) - 2
        if size <= available:
            yield start, end
        else:
            yield from _small_spans(text, start, end, min(240, available))


def _rationale_weight(start, end, sentence_count, headings):
    if sentence_count < 4:
        return 1.0
    # Long abstracts commonly open with a title or background statement. Result
    # sections carry observations across sentences, not just in the heading itself.
    weight = 0.35 if start == 0 else 1.0
    labels = [heading.group() for heading in headings if heading.end() <= end]
    if labels:
        weight *= 1.6 if labels[-1] in {"RESULTS", "FINDINGS", "CONCLUSION", "CONCLUSIONS", "INTERPRETATION"} else 0.6
    return weight


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


def _rank_sentences(query_text, groups, max_bytes=None):
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
            sentence_count = sum(1 for _ in _spans(hit.text))
            headings = list(
                re.finditer(
                    r"\b(?:BACKGROUND|OBJECTIVES?|METHODS|DESIGN|RESULTS|FINDINGS|CONCLUSIONS?|INTERPRETATION)\b",
                    hit.text,
                )
            )
            for start, end in _bounded_spans(hit.text, address, max_bytes):
                candidates.append((
                    address,
                    hit.text,
                    start,
                    end,
                    Counter(_terms(hit.text[start:end])),
                    _rationale_weight(start, end, sentence_count, headings),
                ))
    df = Counter(term for terms in document_terms for term in terms)
    weights = {term: math.log(1 + (len(document_terms) - df[term] + 0.5) / (df[term] + 0.5)) for term in query}
    ranked = []
    for index, (address, text, start, end, terms, rationale_weight) in enumerate(candidates):
        overlap = query & terms.keys()
        # Scientific prose often alternates derived forms (activate/activation).
        # Prefix matches are weaker than complete words and never change the text.
        prefixes = {term[:5] for term in terms if len(term) >= 6}
        related = {term for term in query - overlap if len(term) >= 6 and term[:5] in prefixes}
        if not overlap and not related:
            continue
        score = sum(weights[term] * (1 + math.log(terms[term])) for term in overlap)
        score += sum(0.75 * weights[term] for term in related)
        score *= 1 + (len(overlap) + 0.75 * len(related)) / max(len(query), 1)
        score /= max(1, len(text[start:end].encode()) / 300) ** 0.25
        sentence = text[start:end].lower()
        if re.match(r"\s*(?:background|objective|purpose|aim)\b", sentence):
            score *= 0.65
        if re.search(r"\b(?:results?|conclusions?|findings|found|showed|demonstrated|revealed)\b", sentence):
            score *= 1.25
        score *= 1 + 0.25 * start / max(1, len(text))
        score *= rationale_weight
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
    return f"{TRUST_POLICY}\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{payload}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


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


def _put_state(states, key, value):
    if key not in states or value[0] > states[key][0]:
        states[key] = value


def _prune_states(states):
    # For the same open/closed interval state, a more expensive selection with
    # no greater utility can never improve any subsequent continuation.
    result = {}
    for active in (False, True):
        best = -1.0
        for key in sorted(key for key in states if key[1] == active):
            if states[key][0] > best:
                result[key] = states[key]
                best = states[key][0]
    return result


def _choose_fragments(groups, ranked, max_bytes, *, split_long=False):
    """Exact byte-budget packing, including the cost saved by adjacent intervals."""
    from powercontext.builtin.runtime.prepared_context import MemoryEntryAddress

    scores = {(_key(row[2]), row[4], row[5]): -row[0] for row in ranked}
    base = len(_render_fragments([]).encode())
    # Charge a comma per item and refund the first comma at the outer envelope.
    limit = max_bytes - base + 1
    states = {(0, False): (0.0, ())}
    items = []
    seen = set()
    for group in groups:
        if group.memory_ref is None:
            continue
        for hit in group.hits:
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=group.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            if _key(address) in seen:
                continue
            seen.add(_key(address))
            closed = {}
            for (cost, _), value in states.items():
                _put_state(closed, (cost, False), value)
            states = _prune_states(closed)
            previous_end = 0
            for start, end in _bounded_spans(hit.text, address, max_bytes if split_long else None):
                index = len(items)
                value = scores.get((_key(address), start, end), 0.0)
                items.append((-value, index, address, hit.text, start, end))
                fragment = EvidenceFragment(address, start, end, hit.text[start:end])
                opening = len(_render_fragments([fragment]).encode()) - base + 1
                extension = (
                    len(json.dumps(hit.text[previous_end:end], ensure_ascii=False).encode())
                    - 2
                    + len(str(end))
                    - len(str(previous_end))
                )
                next_states = {}
                for (used, active), (score, chosen) in states.items():
                    _put_state(next_states, (used, False), (score, chosen))
                    cost = used + (extension if active else opening)
                    if cost <= limit:
                        _put_state(next_states, (cost, True), (score + value, (*chosen, index)))
                states = _prune_states(next_states)
                previous_end = end
    _, chosen = max(states.values(), key=lambda value: value[0])
    return _merge_fragments(items, chosen)


def _expand_fragments(selected, ranked, max_bytes):
    """Spend residual bytes on adjacent original words, without opening a citation."""
    sources = {_key(row[2]): row[3] for row in ranked}
    while selected:
        additions = []
        for index, fragment in enumerate(selected):
            text = sources[_key(fragment.address)]
            neighbors = []
            before = re.search(r"\S+\s*$", text[: fragment.start])
            after = re.match(r"\s*\S+\s*", text[fragment.end :])
            if before:
                neighbors.append((before.start(), fragment.end))
            if after:
                neighbors.append((fragment.start, fragment.end + after.end()))
            for start, end in neighbors:
                proposal = list(selected)
                proposal[index] = EvidenceFragment(fragment.address, start, end, text[start:end])
                # Expansion can join two intervals and release a second citation.
                items = [(0, i, f.address, sources[_key(f.address)], f.start, f.end) for i, f in enumerate(proposal)]
                proposal = _merge_fragments(items, range(len(items)))
                size = len(_render_fragments(proposal).encode())
                if size > max_bytes:
                    continue
                added_start, added_end = (start, fragment.start) if start < fragment.start else (fragment.end, end)
                score = max(
                    (
                        -item[0]
                        for item in ranked
                        if _key(item[2]) == _key(fragment.address) and item[4] < added_end and added_start < item[5]
                    ),
                    default=0,
                )
                additions.append((score, -index, proposal))
        if not additions:
            break
        selected = max(additions, key=lambda item: item[:2])[2]
    return selected


def build_evidence_context(request, groups):
    from powercontext.builtin.runtime.prepared_context import PreparedContextBuild

    ranked = _rank_sentences(request.query, groups)
    selected = _choose_fragments(groups, ranked, request.max_bytes)
    if not selected:
        # Prefer intact evidence statements. If none fits, a long unpunctuated
        # paragraph can still supply an exact, useful window around query terms.
        ranked = _rank_sentences(request.query, groups, request.max_bytes)
        selected = _choose_fragments(groups, ranked, request.max_bytes, split_long=True)
    if not selected:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    selected = _expand_fragments(selected, ranked, request.max_bytes)
    content = _render_fragments(selected)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode())),
        origins=tuple(fragment.address for fragment in selected),
        fragments=tuple(selected),
    )
