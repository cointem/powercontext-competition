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


# Vocabulary expansions contain no document identities or evidence locations.
_FINANCIAL_TERMS = (
    (
        r"turnover|return on assets|\broa\b|capital.intensive",
        "income revenue sales cost assets property equipment inventory inventories balance cash flow",
    ),
    (r"debt|borrowing", "debt borrowings notes long term short term maturities"),
    (r"growth", "revenue sales growth increase results operations"),
    (
        r"cash\s*flow|capex|capital expenditure|ebitda",
        "cash flows operating investing financing purchases property equipment depreciation amortization",
    ),
    (
        r"margin|profitab|ebitda|income statement|p&l",
        "income operations earnings revenue sales costs expense profit consolidated",
    ),
    (
        r"quick ratio|current ratio|liquidity|balance sheet|ppne|pp&e",
        "balance sheets financial position current assets liabilities cash receivables inventories property plant equipment net",
    ),
    (r"legal|litigat|lawsuit|battles", "legal proceedings litigation contingencies lawsuits claims court"),
    (
        r"geograph|countries|territor|where.*operat",
        "geographic countries international markets segments region foreign domestic",
    ),
    (r"acqui|merger", "acquisitions acquisition business combinations purchase goodwill"),
    (r"tax.*rate", "income taxes effective tax rate provision reconciliation"),
    (r"customer.*concentration", "customers concentration significant percent revenue sales"),
)

_SECTION_QUERIES = (
    (
        r"turnover|return on assets|\broa\b|capital.intensive",
        r"consolidated\s*(?:balance\s*sheets?|statements?\s*of\s*(?:income|operations|earnings|cash\s*flows?|financial\s*position))",
    ),
    (r"acqui|merger", r"(?:business combinations|acquisitions|acquired businesses)"),
    (r"debt|borrowing", r"(?:long.term debt|short.term borrowings|debt and credit)"),
    (r"cash\s*flow|capex|capital expenditure|ebitda", r"(?:consolidated\s+)?statements?\s+of\s+cash\s*flows?"),
    (
        r"margin|profit|ebitda|income statement|statement of income|p&l|tax.*rate|net income",
        r"consolidated\s+statements?\s+of\s+(?:income|operations|earnings)",
    ),
    (
        r"quick ratio|current ratio|liquidity|balance sheet|ppne|pp&e",
        r"consolidated\s+(?:balance\s+sheets?|statements?\s+of\s+financial\s+position)",
    ),
    (
        r"geograph|countries|territor",
        r"geographic(?:al)?\s+(?:operations|information|segments)|division[s ]|our business",
    ),
    (
        r"categor|segment|products.*revenue|services.*revenue",
        r"(?:summary of )?business\s+segment\s+(?:data|information)",
    ),
    (r"legal|lawsuit|litigat|battles", r"legal\s+proceedings"),
)


def _query_parts(question):
    query = re.sub(r"\bif\b.*?\bthen\b.*?(?:[.!?]|$)", " ", question, flags=re.I)
    query = re.sub(r"\b(?:assume|answer|give a response|respond)\b[^:?!]*[:?!]", " ", query, flags=re.I)
    expanded = " ".join(words for pattern, words in _FINANCIAL_TERMS if re.search(pattern, question, re.I))
    return query, expanded


def _document_company(document_id):
    identity = re.split(r"[_ -](?:19|20)\d{2}", document_id)[0]
    return re.sub(r"[^a-z0-9]", "", identity.lower())


def _document_affinity(document_id, question):
    company = _document_company(document_id)
    compact = re.sub(r"[^a-z0-9]", "", question.lower())
    matched = bool(
        company and (company in re.findall(r"[a-z0-9]+", question.lower()) if len(company) <= 4 else company in compact)
    )
    report_years = re.findall(r"(?:19|20)\d{2}", document_id)
    query_years = [int(year) for year in re.findall(r"(?:19|20)\d{2}", question)]
    query_years.extend(2000 + int(year) for year in re.findall(r"\bfy['\u2019 ]?(\d{2})\b", question, flags=re.I))
    distance = abs(int(report_years[0]) - max(query_years)) if report_years and query_years else 0
    return matched, -distance


def _rank_pages(entries, question):
    documents = [record.entry.text.split("\n", 1)[0] for record in entries]
    affinities = {doc: _document_affinity(doc, question) for doc in documents}
    best = max(affinities.values())
    # An acronym not present in the filename can still identify a report through
    # its own text (cover pages, legal names and URLs). Do not maintain a company map.
    if not best[0]:
        aliases = {
            token.lower()
            for token in re.findall(r"\b[A-Za-z]{2,12}\b", question)
            if sum(letter.isupper() for letter in token) >= 2
        }
        alias_documents = {
            alias: {
                _document_company(documents[i])
                for i, record in enumerate(entries)
                if re.search(r"\b" + alias + r"\b", record.entry.text, re.I)
            }
            for alias in aliases
        }
        identified = {next(iter(docs)) for docs in alias_documents.values() if len(docs) == 1}
        if len(identified) == 1:
            affinities = {
                doc: (_document_company(doc) in identified, affinity[1]) for doc, affinity in affinities.items()
            }
            best = max(affinities.values())
    indices = [i for i, doc in enumerate(documents) if not best[0] or affinities[doc] == best]
    bodies = [entries[i].entry.text.partition("\n")[2] for i in indices]
    # Report identity selects the corpus, but must not inflate the relevance of
    # otherwise empty or short pages through a repeated document-ID prefix.
    identity_terms = {term for i in indices for term in _terms(documents[i])}
    query, expanded = _query_parts(question)
    weights = {term: 1.0 for term in _terms(query) if term not in identity_terms and not term.isdigit()}
    for term in _terms(expanded):
        if term not in identity_terms:
            weights.setdefault(term, 0.45)
    counts = [Counter(_terms(body)) for body in bodies]
    df = Counter(term for count in counts for term in count)
    average = sum(sum(count.values()) for count in counts) / len(counts) or 1
    scored = []
    for index, body, count in zip(indices, bodies, counts, strict=True):
        length = sum(count.values())
        score = sum(
            weight
            * math.log(1 + (len(counts) - df[term] + 0.5) / (df[term] + 0.5))
            * count[term]
            * 2.2
            / (count[term] + 1.2 * (0.25 + 0.75 * length / average))
            for term, weight in weights.items()
            if count[term]
        )
        # Statement headings disambiguate the source of the requested figures
        # from incidental mentions in narrative notes and risk disclosures.
        heading = body[:700].lower()
        for query_pattern, heading_pattern in _SECTION_QUERIES:
            if re.search(query_pattern, question, re.I) and re.search(heading_pattern, heading):
                score += 12
        scored.append((-score, index))
    return [index for _, index in sorted(scored)]


async def search_pdf_pages(memory, question: str, limit: int = 10):
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    page = await memory.list()
    entries = [
        record for record in page.entries if any(source.source_type == "pdf-page" for source in record.entry.sources)
    ]
    if not entries:
        return ()
    seen = set()
    selected = []
    for index in _rank_pages(entries, question):
        record = entries[index]
        identities = tuple((source.source_type, source.source_id) for source in record.entry.sources)
        if identities in seen:
            continue
        seen.add(identities)
        selected.append(record.citation)
        if len(selected) == limit:
            break
    return tuple(selected)
