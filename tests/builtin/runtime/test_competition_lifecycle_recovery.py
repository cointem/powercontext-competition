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

"""A failed native state write remains replayable after process restart."""

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


def test_lifecycle_partial_failure_replays_after_restart_without_rewriting_versions(tmp_path, monkeypatch):
    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Recovery", summary="Source withdrawal", idempotency_key="recovery")
            )
            sid = scope.scope_id
            service = runtime.lifecycle.for_scope(sid)
            receipt = await runtime.sources.for_scope(sid).capture(
                CaptureSource(source_id="source", content="Original evidence", metadata={})
            )
            await service.register_source("source", receipt.source_ref, revision=1)
            originals = []
            for name in ("alpha", "beta"):
                mutation = await runtime.memory.for_scope(sid).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"{name} evidence"),))
                )
                assert mutation.entry is not None
                originals.append(mutation.entry.citation)
                await service.register_memory(name, mutation.entry.citation, parents=("source",), policy="all")
            original_forget = MemoryService.forget
            attempted = 0

            async def fail_second(self, *args, **kwargs):
                nonlocal attempted
                attempted += 1
                if attempted == 2:
                    raise RuntimeError("injected native state failure")  # noqa: TRY003
                return await original_forget(self, *args, **kwargs)

            withdrawal = SourceLifecycleEvent(
                node_id="source", revision=1, sequence=1, available=False, reason="withdrawn"
            )
            with monkeypatch.context() as patch:
                patch.setattr(MemoryService, "forget", fail_second)
                with pytest.raises(RuntimeError, match="injected native state failure"):
                    await service.apply(withdrawal)
            rows = (await runtime.memory.for_scope(sid).list(include_inactive=True)).entries
            assert sum(row.state == "active" for row in rows) == 1
        async with open_builtin_runtime(config) as runtime:
            service = runtime.lifecycle.for_scope(sid)
            assert await service.apply(withdrawal) == []
            rows = await runtime.memory.for_scope(sid).list(include_inactive=True)
            assert all(row.state != "active" for row in rows.entries)
            assert {row.entry.entry_version_id for row in rows.entries} == {ref.entry_version_id for ref in originals}
            before = rows.memory_ref
            assert await service.apply(withdrawal) == []
            assert (await runtime.memory.for_scope(sid).list(include_inactive=True)).memory_ref == before
            assert await service.apply(
                SourceLifecycleEvent(node_id="source", revision=1, sequence=2, available=True, reason="restored")
            ) == ["alpha", "beta", "source"]
            for name, reference in zip(("alpha", "beta"), originals, strict=True):
                assert await service.resolve(name, purpose="generation") == reference

    asyncio.run(run())
