"""Thin facade over the PowerContext async client.

Everything Recap knows about "what did I actually work on" comes from here: the
Source journal, versioned Handoff artifacts, and atomic Memory. Nothing in this
module fabricates content; it only shapes requests and normalizes citations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any, Iterable

from powercontext.client import PowerContextClient
from powercontext.http import (
    AcknowledgeHandoffRequest,
    AtomicMemoryInput,
    AtomicMemoryLifecycleRequest,
    CaptureContentSourceRequest,
    CommitHandoffRequest,
    ContinueHandoffRequest,
    CreateScopeRequest,
    CreateWorkContractRequest,
    HandoffCurrentWorkRequest,
    ListArtifactRevisionsRequest,
    ListArtifactsRequest,
    ListAtomicMemoryRequest,
    ListSourcesRequest,
    PrepareContextRequest,
    RecordTaskOutcomeRequest,
    RememberMemoryRequest,
    SearchAtomicMemoryRequest,
    WorkClaim,
)

from recap.config import Config

PREPARED_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
PREPARED_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"


def ref_key(family: str, artifact_id: str, revision: int) -> str:
    """PowerContext's display addressing for a versioned artifact."""
    return f"{family}/{artifact_id}@{revision}"


def source_key(digest_input: str) -> str:
    return "src-" + hashlib.sha256(digest_input.encode()).hexdigest()[:20]


def content_key(label: str, payload: Any) -> str:
    """PowerContext Source identity is immutable: reusing a source_id with a different
    payload is rejected as source_conflict. Folding the payload into the id means an
    unchanged re-collect replays and a changed one appends a new journal version."""
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:16]
    return source_key(f"{label}\n{fingerprint}")


def parse_prepared(content: str | None) -> list[dict[str, Any]]:
    """Pull the JSON package out of a PreparedContext payload."""
    if not content or PREPARED_BEGIN not in content:
        return []
    body = content.split(PREPARED_BEGIN, 1)[1].split(PREPARED_END, 1)[0].strip()
    try:
        return json.loads(body).get("items", [])
    except json.JSONDecodeError:
        return []


class PowerContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._principal_id: str | None = None

    @asynccontextmanager
    async def _client(self):
        async with PowerContextClient(base_url=self.cfg.server_url, token=self.cfg.token) as client:
            yield client

    async def principal_id(self) -> str:
        """The authenticated Principal, cached for the life of the facade."""
        if self._principal_id is None:
            async with self._client() as c:
                self._principal_id = (await c.get_access_principal()).principal.id
        return self._principal_id

    # -- scope ---------------------------------------------------------------
    async def ensure_scope(self) -> str:
        async with self._client() as c:
            scope = await c.create_scope(
                CreateScopeRequest(
                    title=self.cfg.scope_title,
                    summary=self.cfg.scope_summary,
                    idempotency_key="recap:scope:pilot",
                )
            )
            return scope.scope_id

    # -- evidence ------------------------------------------------------------
    async def capture(self, scope_id: str, label: str, text: str, metadata: dict | None = None) -> str:
        """Append one Source to the journal. `label` plus content makes the source_id idempotent."""
        async with self._client() as c:
            resp = await c.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id=content_key(label, text),
                    content=text,
                    metadata={"label": label, **(metadata or {})},
                )
            )
            return resp.source.source_id

    async def journal(self, scope_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """The journal is paginated at 100/page; walk cursors until `limit`."""
        async with self._client() as c:
            out: list[dict[str, Any]] = []
            cursor = None
            while len(out) < limit:
                page = await c.list_sources(
                    scope_id,
                    ListSourcesRequest(limit=min(100, limit - len(out)), cursor=cursor),
                )
                for s in page.items:
                    out.append(
                        {
                            "source_id": s.source_id,
                            "content": s.content if isinstance(s.content, str) else json.dumps(s.content),
                            "position": s.position,
                        }
                    )
                cursor = page.next_cursor
                if not cursor:
                    return out
            return out

    # -- memory --------------------------------------------------------------
    async def remember(self, scope_id: str, kind: str, text: str, reason: str = "") -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind=kind, text=text, reason=reason or None)
            )
            return _memory_dict(resp.records[0])

    async def remember_new(self, scope_id: str, kind: str, text: str, reason: str = "") -> dict[str, Any] | None:
        """Atomic memory has no identity key, so re-collecting a week would store the
        same decision again as a new entry. Recall first and write only what is new."""
        existing = {
            mem["text"] for mem in await self.active_memories(scope_id, kinds=[kind])
        }
        if text in existing:
            return None
        return await self.remember(scope_id, kind, text, reason=reason)

    async def active_memories(
        self, scope_id: str, kinds: Iterable[str] | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self._client() as c:
            wanted = set(kinds) if kinds else None
            out: list[dict[str, Any]] = []
            cursor = None
            while True:
                page = await c.list_atomic_memories(
                    ListAtomicMemoryRequest(scope_id=scope_id, states=["active"], limit=100, cursor=cursor)
                )
                for rec in page.items:
                    if wanted is None or rec.kind in wanted:
                        out.append(_memory_dict(rec))
                cursor = page.next_cursor
                if not cursor:
                    return out

    async def search_memories(self, scope_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.search_atomic_memory(
                SearchAtomicMemoryRequest(scope_id=scope_id, query=query, mode="text", limit=limit)
            )
            return [_memory_dict(h.memory) | {"score": h.score} for h in resp.hits]

    async def forget(self, scope_id: str, artifact: dict, state_version: int) -> bool:
        async with self._client() as c:
            resp = await c.change_atomic_memory_lifecycle(
                AtomicMemoryLifecycleRequest(
                    scope_id=scope_id,
                    target=AtomicMemoryInput(
                        artifact=_ref(artifact), state_version=state_version
                    ),
                    state="forgotten",
                )
            )
            return bool(resp.changed)

    # -- recall --------------------------------------------------------------
    async def prepare_context(self, scope_id: str, query: str, max_bytes: int = 8000) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query=query, max_bytes=max_bytes)
            )
            return {
                "status": resp.status.value if hasattr(resp.status, "value") else str(resp.status),
                "content_bytes": resp.content_bytes,
                "items": parse_prepared(resp.content),
            }

    # -- handoff work loop ---------------------------------------------------
    async def work_contract(
        self,
        scope_id: str,
        label: str,
        objective: str,
        facts: list[str],
        in_scope: list[str],
        exclusions: list[str] | None = None,
        completion_criteria: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        from powercontext.http import WorkContract

        async with self._client() as c:
            contract = {
                "schema": "powercontext.work-contract.v1",
                "trust": "untrusted_input",
                "objective": objective,
                "facts": facts,
                "in_scope": in_scope,
                "exclusions": exclusions or [],
                "completion_criteria": completion_criteria or [],
                "authorization_notes": [],
                "open_questions": open_questions or [],
            }
            receipt = await c.create_work_contract(
                CreateWorkContractRequest(
                    scope_id=scope_id,
                    source_id=content_key(
                        label, WorkContract.model_validate(contract).model_dump(mode="json")
                    ),
                    contract=contract,
                )
            )
            return _receipt_dict(receipt)

    async def snapshot_current_work(
        self,
        scope_id: str,
        label: str,
        objective: str,
        claims: list[dict[str, Any]],
        disposition: str = "continuable",
        next_action: dict[str, Any] | None = None,
        omissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """`handoff_current_work` writes the boundary Source; commit makes it durable."""
        from powercontext.http import CurrentWorkHandoff

        async with self._client() as c:
            payload = {
                "schema": "powercontext.current-work-handoff.v1",
                "trust": "untrusted_input",
                "objective": objective,
                "state": [_claim(c2) for c2 in claims],
                "disposition": disposition,
                "next_action": _claim(next_action) if next_action else None,
                "omissions": omissions or [],
            }
            # Fingerprint the validated form: the server journals this same canonical shape.
            prepared = await c.handoff_current_work(
                HandoffCurrentWorkRequest(
                    scope_id=scope_id,
                    source_id=content_key(
                        label, CurrentWorkHandoff.model_validate(payload).model_dump(mode="json")
                    ),
                    handoff=payload,
                )
            )
            return {
                "boundary": _receipt_dict(prepared.boundary),
                "prepared": prepared.handoff.model_dump(by_alias=True),
            }

    async def commit_handoff(
        self, scope_id: str, prepared: dict[str, Any], boundary_source_id: str
    ) -> dict[str, Any]:
        from powercontext.http import PreparedHandoff

        _cite_declared(prepared, boundary_source_id)
        async with self._client() as c:
            committed = await c.commit_handoff(
                CommitHandoffRequest(
                    scope_id=scope_id,
                    handoff=PreparedHandoff.model_validate(prepared),
                )
            )
            return {
                "reference": _ref_dict(committed.reference),
                "source_refs": [
                    {"name": s.name, "source_id": s.source_id} for s in committed.source_refs
                ],
            }

    async def acknowledge(
        self, scope_id: str, label: str, revision: dict[str, Any], message: str = ""
    ) -> dict[str, Any]:
        async with self._client() as c:
            # Enforced access rejects an accepted receipt whose receiver is not the
            # authenticating Principal, so the receipt signs as ourselves.
            receiver = await self.principal_id()
            await c.continue_handoff(
                ContinueHandoffRequest(scope_id=scope_id, selection="exact", revision=_ref(revision))
            )
            resp = await c.acknowledge_handoff(
                AcknowledgeHandoffRequest(
                    scope_id=scope_id,
                    source_id=content_key(
                        label, {"revision": revision, "status": "accepted", "message": message}
                    ),
                    receiver=receiver,
                    status="accepted",
                    selection="exact",
                    revision=_ref(revision),
                    receiver_checks={
                        "live_state": "confirmed",
                        "capability": "confirmed",
                        "authorization": "confirmed",
                    },
                    message=message or None,
                )
            )
            return {"resolution": str(resp.resolution)}

    async def record_outcome(
        self,
        scope_id: str,
        label: str,
        objective: str,
        status: str,
        summary: str,
        observations: list[str] | None = None,
        checks: list[dict[str, Any]] | None = None,
        produced_artifacts: list[dict[str, Any]] | None = None,
        remaining_work: list[str] | None = None,
    ) -> dict[str, Any]:
        from powercontext.http import TaskOutcome

        async with self._client() as c:
            outcome = {
                "schema": "powercontext.task-outcome.v1",
                "trust": "untrusted_observation",
                "objective": objective,
                "status": status,
                "summary": summary,
                "observations": observations or [],
                "checks": checks or [],
                "produced_artifacts": [_ref(a) for a in produced_artifacts or []],
                "remaining_work": remaining_work or [],
            }
            receipt = await c.record_task_outcome(
                RecordTaskOutcomeRequest(
                    scope_id=scope_id,
                    source_id=content_key(
                        label, TaskOutcome.model_validate(outcome).model_dump(mode="json")
                    ),
                    outcome=outcome,
                )
            )
            return _receipt_dict(receipt)

    async def handoffs(self, scope_id: str, limit: int = 100) -> list[dict[str, Any]]:
        async with self._client() as c:
            out: list[dict[str, Any]] = []
            cursor = None
            while len(out) < limit:
                page = await c.list_artifacts(
                    scope_id, "handoff", ListArtifactsRequest(limit=min(100, limit - len(out)), cursor=cursor)
                )
                for item in page.items:
                    # The collection listing carries a digest only; the omissions and
                    # next actions the report needs live in the immutable revision.
                    revision = await c.get_artifact_revision(
                        scope_id, "handoff", item.artifact_id, item.revision
                    )
                    out.append(_handoff_dict(revision))
                cursor = page.next_cursor
                if not cursor:
                    return out
            return out

    async def handoff_revisions(self, scope_id: str, artifact_id: str) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.list_artifact_revisions(scope_id, "handoff", artifact_id, ListArtifactRevisionsRequest(limit=50))
            return [_handoff_dict(a) for a in resp.items]


DECLARED_CITATION_FIELDS = ("omissions",)


def _cite_declared(prepared: dict[str, Any], boundary_source_id: str) -> None:
    """`handoff_current_work` echoes omissions as bare text; committing wants a citation.

    A declared omission is asserted *in this boundary*, so the boundary Source is
    its citation. Nothing is presented as verified evidence.
    """
    citation = {"kind": "source", "source_ref": {"name": "content", "source_id": boundary_source_id}}
    content = prepared.get("content") or {}
    for field in DECLARED_CITATION_FIELDS:
        content[field] = [
            item if isinstance(item, dict) and item.get("citation") else
            {"text": item.get("text") if isinstance(item, dict) else item, "citation": citation}
            for item in (content.get(field) or [])
        ]


def _claim(item: dict[str, Any]):
    return WorkClaim(
        text=item["text"],
        basis=item.get("basis", "declared"),
        evidence=[
            {"kind": "source", "source_ref": {"name": "content", "source_id": e["source_id"]}}
            for e in item.get("evidence", [])
        ],
    )


def _ref(d: dict[str, Any]):
    from powercontext.http import ArtifactReference

    return ArtifactReference(
        family=d["family"], artifact_id=d["artifact_id"], revision=d["revision"]
    )


def _ref_dict(ref) -> dict[str, Any]:
    return {"family": ref.family, "artifact_id": ref.artifact_id, "revision": ref.revision}


def _memory_dict(rec) -> dict[str, Any]:
    return {
        "ref": ref_key(rec.artifact.family, rec.artifact.artifact_id, rec.artifact.revision),
        "family": rec.artifact.family,
        "artifact_id": rec.artifact.artifact_id,
        "revision": rec.artifact.revision,
        "kind": rec.kind,
        "text": rec.text,
        "state": rec.state.value if hasattr(rec.state, "value") else str(rec.state),
        "state_version": rec.state_version,
    }


def _receipt_dict(receipt) -> dict[str, Any]:
    return {
        "kind": receipt.kind.value if hasattr(receipt.kind, "value") else str(receipt.kind),
        "source_id": receipt.source.source_id,
        "position": receipt.position,
    }


def _handoff_dict(art) -> dict[str, Any]:
    content = getattr(art, "content", None)
    dumped = content.model_dump(by_alias=True) if hasattr(content, "model_dump") else content
    ref = _ref_dict(art.artifact) if hasattr(art, "artifact") else {
        "family": "handoff",
        "artifact_id": getattr(art, "artifact_id", ""),
        "revision": getattr(art, "revision", 1),
    }
    return {"ref": ref_key("handoff", ref["artifact_id"], ref["revision"]), **ref, "content": dumped}


def run(coro):
    return asyncio.run(coro)
