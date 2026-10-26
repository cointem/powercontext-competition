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

"""Capture immutable visible Pi events across branches and durable checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity(session_id, event_id):
    return "pi:" + hashlib.sha256(_canonical([session_id, event_id]).encode()).hexdigest()


def _records(value):
    try:
        rows = [json.loads(line) for line in value.content.decode("utf-8-sig").splitlines() if line.strip()]
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid Pi JSONL") from error  # noqa: TRY003
    if not rows or not isinstance(rows[0], dict):
        raise ValueError("Missing Pi session header")  # noqa: TRY003
    header = rows[0]
    if (
        header.get("type") != "session"
        or type(header.get("version")) is not int
        or header["version"] != 3
        or not isinstance(header.get("id"), str)
        or not header["id"].strip()
    ):
        raise ValueError("Invalid Pi session header")  # noqa: TRY003
    by_id = {}
    for row in rows[1:]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("Invalid Pi event ID")  # noqa: TRY003
        if row["id"] in by_id or "parentId" not in row:
            raise ValueError("Duplicate event or missing parent")  # noqa: TRY003
        parent = row["parentId"]
        if parent is not None and (not isinstance(parent, str) or parent not in by_id):
            raise ValueError("Pi parent must precede its child")  # noqa: TRY003
        by_id[row["id"]] = row
    return header["id"], by_id


def _blocks(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            blocks.append(block["text"])
        elif block.get("type") == "toolCall":
            blocks.append(
                f"[tool_call {block.get('name', '')} {block.get('id', '')}] " + _canonical(block.get("arguments", {}))
            )
    return "\n".join(blocks)


def _event(session_id, row):
    kind = row.get("type")
    if kind == "message":
        message = row.get("message", {})
        if not isinstance(message, dict):
            raise ValueError("Invalid Pi message")  # noqa: TRY003
        role = message.get("role", "")
        body = _blocks(message.get("content"))
        if role == "toolResult":
            body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {body}"
        elif role == "bashExecution":
            body = f"$ {message.get('command', '')}\n{message.get('output', '')}"
    elif kind in {"compaction", "branch_summary"}:
        role, body = kind, row.get("summary", "")
    elif kind == "custom_message":
        role, body = "custom", _blocks(row.get("content"))
    else:
        return None
    if not isinstance(body, str) or not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=row["id"],
        parent_id=row["parentId"],
        event_type=kind,
        role=role,
        content=f"{role}: {body}",
        record=row,
    )


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    session_id, by_id = _records(value)
    leaf = value.leaf_id if value.leaf_id is not None else next(reversed(by_id), None)
    if leaf is not None and leaf not in by_id:
        raise ValueError("Unknown Pi leaf")  # noqa: TRY003
    chain = []
    while leaf is not None:
        row = by_id[leaf]
        event = _event(session_id, row)
        if event is not None:
            chain.append(event)
        leaf = row["parentId"]
    return tuple(reversed(chain))


class PiEventSource(Source, PiEventInput):
    pass


class _PiProjection:
    name = "powercontext.text-evidence"
    version = "1"
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source):
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                key: getattr(source, key) for key in ("session_id", "event_id", "parent_id", "event_type", "role")
            },
        )


class _PiDefinition:
    name = "pi-event"
    version = "1"
    input_class = PiEventInput
    source_class = PiEventSource
    projections = (_PiProjection(),)

    async def resolve(self, value, /):
        return PiEventSource(
            **value.model_dump(),
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source, /):
        return PiEventInput.model_validate(source.model_dump())


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(_PiDefinition(), projections=(_PiProjection(),))


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path, leaf_id=None):
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        value = PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id)
        events = parse_pi_session(value)
        session_id, records = _records(value)
        checkpoint = session.checkpoint
        if checkpoint is not None and (
            not isinstance(checkpoint, dict) or any(not isinstance(v, str) for v in checkpoint.values())
        ):
            raise ValueError("Invalid Pi checkpoint")  # noqa: TRY003
        imported = dict(checkpoint or {})
        # Validate all previously imported records present in the file, including other branches.
        hashes = {
            _identity(session_id, key): hashlib.sha256(_canonical(row).encode()).hexdigest()
            for key, row in records.items()
        }
        for identity, digest in imported.items():
            if identity in hashes and hashes[identity] != digest:
                raise ValueError("An imported Pi event was rewritten")  # noqa: TRY003
        for event in events:
            identity = _identity(event.session_id, event.event_id)
            if identity not in imported:
                await session.submit(identity, "pi-event", event)
                imported[identity] = hashes[identity]
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=imported)
