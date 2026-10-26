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

"""Regression cases for literal evidence selection and financial page retrieval."""

from __future__ import annotations

import asyncio

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryHit
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


def _prepare(query, texts, budget=1400):
    ref = ArtifactRef(family="memory", artifact_id="memory", revision=7)
    hits = tuple(
        MemoryHit(
            memory_ref=ref,
            entry_id=str(index),
            entry_version_id=f"version-{index}",
            text=text,
            score=1.0,
            matched_by=("fts",),
        )
        for index, text in enumerate(texts)
    )
    return PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True),
        current_scope_id="quality",
        memory_candidates=(PreparedMemoryCandidates(scope_id="quality", memory_ref=ref, hits=hits),),
    )


def test_scientific_inflections_select_literal_results():
    text = "Background: Cell growth requires nutrients. Results: Proliferative cell growth was inhibited."
    result = _prepare("proliferation inhibition", (text,))
    assert result.fragments
    assert any("was inhibited" in fragment.text for fragment in result.fragments)
    for fragment in result.fragments:
        assert fragment.text == text[fragment.start : fragment.end]
    assert result.context.content_bytes == len(result.context.content.encode()) <= 1400


def test_unaffordable_high_ranked_sentences_do_not_hide_short_evidence():
    huge = "Sensor calibration cooling " + "experimental conditions " * 200 + "."
    short = "The calibration result was 37 degrees."
    result = _prepare("sensor calibration cooling", (*[huge + str(i) for i in range(20)], short), budget=900)
    assert result.fragments
    assert any(fragment.text == short for fragment in result.fragments)
    assert result.context.content_bytes <= 900


def test_abbreviation_does_not_cut_a_scientific_evidence_sentence():
    text = "Results: The mutation activates several pathways, e.g. the kinase pathway in liver cells."
    result = _prepare("mutation activates pathways", (text,))
    assert len(result.fragments) == 1
    assert result.fragments[0].text == text


class _PagePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="page",
                text=TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY)).content,
                sources=(source,),
            )
            for source in request.sources
        )


def test_financial_statement_beats_repeated_report_identity_and_query_boilerplate():
    async def run():
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="Quality", idempotency_key="quality")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            statement = "Aster Corporation (ASTCO)\nConsolidated Statements of Income\nNet revenue 1000\nCost of sales 600\nOperating income 400"
            records = [
                ("ASTER_2025_10K", i, "Operating margin profile targets are a useful metric for analysts. " * 4)
                for i in range(1, 26)
            ]
            records += [("ASTER_2024_10K", 1, statement), ("ASTER_2025_10K", 26, statement)]
            observations = {}
            for doc, number, body in records:
                source = await registry.resolve(
                    PDFPageInput(document_id=doc, document_sha256="a" * 64, page_number=number, text=body)
                )
                observation = project_source_for_transport(registry, source)
                observations[source.name] = (doc, number)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
            citations = await search_pdf_pages(
                memory,
                "Does Aster have an improving operating margin profile as of FY2025? If operating margin is not a useful metric for a company like this, then state that and explain why.",
                limit=1,
            )
            assert len(citations) == 1
            entry = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
            assert entry.entry.text.endswith(statement)
            assert [observations[source.source_id] for source in entry.entry.sources] == [("ASTER_2025_10K", 26)]
            alias_citations = await search_pdf_pages(memory, "What is the FY2025 operating margin for ASTCO?", limit=1)
            assert alias_citations == citations

    asyncio.run(run())
