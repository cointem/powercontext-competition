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

"""Durable source dependency graphs with resumable native state propagation."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE as NODES
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.sources import SourceRef


class SourceLifecycleError(ValueError):
    """Invalid lifecycle graph, reference, or event."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Lifecycle strings must be nonblank")  # noqa: TRY003
        return value


class SourceLifecycleApplication:
    def __init__(self, runtime, database):
        self.runtime = runtime
        self.database = database
        self.locks = defaultdict(asyncio.Lock)

    def for_scope(self, scope_id):
        from powercontext.builtin.scope.models import validate_scope_id

        return ScopedSourceLifecycle(self, validate_scope_id(scope_id))


class ScopedSourceLifecycle:
    def __init__(self, application, scope_id):
        self.application = application
        self.runtime = application.runtime
        self.database = application.database
        self.scope_id = scope_id

    async def _load(self):
        async with self.database.transaction() as connection:
            rows = (await connection.execute(select(NODES).where(NODES.c.scope_id == self.scope_id))).mappings()
            return {row["node_id"]: json.loads(row["payload"]) for row in rows}

    async def _save(self, node_id, node, *, new=False):
        payload = json.dumps(node, ensure_ascii=False, separators=(",", ":")).encode()
        reference_id = node["reference"].get(
            "entry_id", node["reference"].get("artifact_id", node["reference"].get("source_id"))
        )
        values = {
            "kind": node["kind"],
            "reference_id": reference_id,
            "available": node["available"],
            "payload": payload,
        }
        async with self.database.transaction() as connection:
            if new:
                await connection.execute(insert(NODES).values(scope_id=self.scope_id, node_id=node_id, **values))
            else:
                await connection.execute(
                    update(NODES).where(NODES.c.scope_id == self.scope_id, NODES.c.node_id == node_id).values(**values)
                )

    async def register_source(self, node_id, source_ref, *, revision=1):
        if type(revision) is not int or revision <= 0 or not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("Invalid source lifecycle binding")  # noqa: TRY003
        await self._register(node_id, "source", source_ref, (), "all", revision)

    async def register_memory(self, node_id, citation, *, parents, policy="all"):
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("Expected a MemoryCitation")  # noqa: TRY003
        await self._register(node_id, "memory", citation, parents, policy, None)

    async def register_topic(self, node_id, artifact_ref, *, parents, policy="all"):
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("Expected a Topic Memory reference")  # noqa: TRY003
        await self._register(node_id, "topic", artifact_ref, parents, policy, None)

    async def _register(self, node_id, kind, reference, parents, policy, revision):
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
            raise SourceLifecycleError("Invalid node ID")  # noqa: TRY003
        if policy not in {"all", "any"} or not isinstance(parents, (tuple, list)):
            raise SourceLifecycleError("Invalid dependency policy")  # noqa: TRY003
        async with self.application.locks[self.scope_id]:
            graph = await self._load()
            if kind != "source" and (
                not parents
                or any(not isinstance(parent, str) for parent in parents)
                or len(set(parents)) != len(parents)
                or any(parent == node_id or parent not in graph for parent in parents)
            ):
                raise SourceLifecycleError("Parents must be distinct, previously registered nodes")  # noqa: TRY003
            binding = {
                "kind": kind,
                "reference": reference.model_dump(mode="json"),
                "parents": list(parents),
                "policy": policy,
                "revision": revision,
            }
            if node_id in graph:
                if any(graph[node_id][key] != value for key, value in binding.items()):
                    raise SourceLifecycleError("Conflicting node registration")  # noqa: TRY003
                await self._reconcile(graph)
                return
            try:
                if kind == "source":
                    await self.runtime.records.for_scope(self.scope_id).get_source(
                        reference.source_type, reference.source_id
                    )
                elif kind == "memory":
                    await self.runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
                else:
                    await self.runtime.records.for_scope(self.scope_id).get_artifact_revision(
                        reference.family, reference.artifact_id, reference.revision
                    )
            except Exception as error:
                raise SourceLifecycleError("Lifecycle reference is not readable in this scope") from error  # noqa: TRY003
            active = _availability(graph)
            available = (
                True
                if kind == "source"
                else (
                    all(active[parent] for parent in parents)
                    if policy == "all"
                    else any(active[parent] for parent in parents)
                )
            )
            node = {**binding, "available": available, "event": None}
            await self._save(node_id, node, new=True)
            graph[node_id] = node
            await self._reconcile(graph)

    async def apply(self, event: SourceLifecycleEvent):
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("Expected a lifecycle event")  # noqa: TRY003
        async with self.application.locks[self.scope_id]:
            graph = await self._load()
            node = graph.get(event.node_id)
            if node is None or node["kind"] != "source" or node["revision"] != event.revision:
                raise SourceLifecycleError("Unknown source or mismatched revision")  # noqa: TRY003
            previous = node["event"]
            current = event.model_dump(mode="json")
            if previous is not None and (
                event.sequence < previous["sequence"]
                or (event.sequence == previous["sequence"] and current != previous)
            ):
                raise SourceLifecycleError("Stale or conflicting event sequence")  # noqa: TRY003
            # Persist intent before changing native state. Replays complete any interrupted propagation.
            node["event"] = current
            node["available"] = event.available
            await self._save(event.node_id, node)
            active = await self._reconcile(graph)
            return sorted(node_id for node_id, available in active.items() if available)

    async def _reconcile(self, graph):
        active = _availability(graph)
        for node_id, node in graph.items():
            desired = active[node_id]
            if node["kind"] == "memory":
                async with self.runtime._context(self.scope_id) as context, self.runtime._locked(self.scope_id):
                    service = context.artifacts.memory
                    head = await service.head(node["reference"]["memory_ref"]["artifact_id"])
                    entry = next(
                        (
                            entry
                            for entry in await service.entries(head)
                            if entry.entry_id == node["reference"]["entry_id"]
                        ),
                        None,
                    )
                    if entry is None or entry.entry_version_id != node["reference"]["entry_version_id"]:
                        raise SourceLifecycleError("Registered memory version is no longer current")  # noqa: TRY003
                    state = next(
                        item.state for item in head.content.manifest.entries if item.entry_id == entry.entry_id
                    )
                    if (state == "active") != desired:
                        if desired:
                            await service.reactivate(head, entries=(entry,))
                        else:
                            await service.forget(head, entries=(entry,), reason="source lifecycle withdrawal")
            if node["available"] != desired:
                node["available"] = desired
                await self._save(node_id, node)
        return active

    async def resolve(self, node_id, *, purpose="generation"):
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("Unknown reference purpose")  # noqa: TRY003
        graph = await self._load()
        node = graph.get(node_id)
        if node is None or (purpose == "generation" and not _availability(graph)[node_id]):
            raise SourceLifecycleError("Node is not available for this purpose")  # noqa: TRY003
        model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[node["kind"]]
        return model.model_validate(node["reference"])


def _availability(graph):
    active = {}
    visiting = set()

    def visit(node_id):
        if node_id in active:
            return active[node_id]
        if node_id in visiting or node_id not in graph:
            raise SourceLifecycleError("Invalid dependency graph")  # noqa: TRY003
        visiting.add(node_id)
        node = graph[node_id]
        if node["kind"] == "source":
            value = node["available"]
        else:
            support = [visit(parent) for parent in node["parents"]]
            value = all(support) if node["policy"] == "all" else any(support)
        visiting.remove(node_id)
        active[node_id] = value
        return value

    for node_id in graph:
        visit(node_id)
    return active
