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

"""Full physical-page PDF capture and offline lexical evidence retrieval."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter

from pydantic import BaseModel, Field, field_validator

from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TextEvidence


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def nonblank_document(cls, value):
        if not value.strip():
            raise ValueError("Document ID must be nonblank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(gt=0, strict=True)
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301 - Normalize all parser failures below.
        digest = hashlib.sha256(value.content).hexdigest()
        return tuple(
            PDFPageInput(
                document_id=value.document_id, document_sha256=digest, page_number=index, text=page.extract_text() or ""
            )
            for index, page in enumerate(reader.pages, 1)
        )
    except Exception as error:
        raise ValueError("Unable to parse the complete PDF") from error  # noqa: TRY003


class PDFPageSource(Source, PDFPageInput):
    pass


class _PDFProjection:
    name = "powercontext.text-evidence"
    version = "1"
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source):
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={key: getattr(source, key) for key in ("document_id", "document_sha256", "page_number")},
        )


class _PDFDefinition:
    name = "pdf-page"
    version = "1"
    input_class = PDFPageInput
    source_class = PDFPageSource
    projections = (_PDFProjection(),)

    async def resolve(self, value, /):
        identity = json.dumps(
            [value.document_id, value.document_sha256, value.page_number], ensure_ascii=False, separators=(",", ":")
        ).encode()
        return PDFPageSource(
            **value.model_dump(),
            name="pdf:" + hashlib.sha256(identity).hexdigest(),
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source, /):
        return PDFPageInput.model_validate(source.model_dump())


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(_PDFDefinition(), projections=(_PDFProjection(),))
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
    "what",
    "how",
    "which",
    "did",
    "does",
    "do",
    "has",
    "have",
    "had",
    "its",
    "their",
    "this",
    "that",
    "it",
    "company",
    "fiscal",
    "year",
    "years",
    "much",
    "many",
}


def _terms(text):
    return [
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in re.findall(r"[a-z]+|\d+", text.lower())
        if word not in _STOP
    ]


async def search_pdf_pages(memory, question: str, limit: int = 10):
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    # The scoped public list preserves exact citations, including entries outside a
    # backend's small coarse search pool. Rank the page corpus without model calls.
    page = await memory.list()
    entries = [
        record for record in page.entries if any(source.source_type == "pdf-page" for source in record.entry.sources)
    ]
    if not entries:
        return ()
    counts = [Counter(_terms(record.entry.text)) for record in entries]
    df = Counter(term for count in counts for term in count)
    average = sum(sum(count.values()) for count in counts) / len(counts) or 1
    query = Counter(_terms(question))
    ranked = []
    for index, (record, count) in enumerate(zip(entries, counts, strict=True)):
        length = sum(count.values())
        score = sum(
            math.log(1 + (len(counts) - df[term] + 0.5) / (df[term] + 0.5))
            * count[term]
            * 2.2
            / (count[term] + 1.2 * (0.25 + 0.75 * length / average))
            for term in query
            if count[term]
        )
        title = set(_terms(record.entry.text.split("\n", 1)[0]))
        # Explicit company/document and year terms anchor the requested report.
        score += sum(3 * math.log(1 + len(counts) / max(df[term], 1)) for term in query if term in title)
        ranked.append((-score, index, record))
    seen = set()
    selected = []
    for _, _, record in sorted(ranked):
        identities = tuple((source.source_type, source.source_id) for source in record.entry.sources)
        if identities in seen:
            continue
        seen.add(identities)
        selected.append(record.citation)
        if len(selected) == limit:
            break
    return tuple(selected)
