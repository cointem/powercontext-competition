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

"""Self-contained, validated UTF-8 context prefix-delta protocol."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

_PACK = "powercontext.context-pack.v1"
_DELTA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Invalid context wire data or chunk boundary."""


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _items(items) -> tuple[bytes, ...]:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("Chunks must be a tuple of bytes")  # noqa: TRY003
    try:
        for item in items:
            item.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("Chunks must be valid UTF-8") from error  # noqa: TRY003
    return items


def _encode(items):
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode(items):
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ContextPackError("Encoded chunks must be an array of strings")  # noqa: TRY003
    try:
        decoded = tuple(base64.b64decode(item, validate=True) for item in items)
        if _encode(decoded) != items:
            raise ContextPackError("Noncanonical Base64")  # noqa: TRY003
        return _items(decoded)
    except (ValueError, UnicodeError) as error:
        raise ContextPackError("Invalid encoded chunks") from error  # noqa: TRY003


def _digest(value):
    return "sha256:" + hashlib.sha256(_json({"schema": _PACK, "items": _encode(value)})).hexdigest()


def _validate_digest(value):
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(c not in "0123456789abcdef" for c in value[7:])
    ):
        raise ContextPackError("Invalid digest")  # noqa: TRY003


def _load(data, fields, schema):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContextPackError("Duplicate field")  # noqa: TRY003
            result[key] = value
        return result

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
        if not isinstance(result, dict) or set(result) != fields or result["schema"] != schema:
            raise ContextPackError("Invalid protocol fields or schema")  # noqa: TRY003
    except (ValueError, TypeError, AttributeError) as error:
        raise ContextPackError("Invalid context wire data") from error  # noqa: TRY003
    else:
        return result


@dataclass(frozen=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items):
        items = _items(items)
        digest = _digest(items)
        size = 0
        while True:
            pack = cls(items, digest, size)
            actual = len(pack.to_bytes())
            if actual == size:
                return pack
            size = actual

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512):
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        if content is None:
            content = ""
        try:
            data = content.encode("utf-8")
        except (AttributeError, UnicodeError) as error:
            raise ContextPackError("Content must be UTF-8 text") from error  # noqa: TRY003
        chunks = []
        start = 0
        while start < len(data):
            end = min(start + chunk_bytes, len(data))
            while end < len(data) and data[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("Chunk bound is smaller than a UTF-8 code point")  # noqa: TRY003
            # Prefer line boundaries so appended sections preserve existing chunks.
            newline = data.rfind(b"\n", start, end)
            if newline >= start:
                end = newline + 1
            chunks.append(data[start:end])
            start = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _PACK,
            "items": _encode(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes):
        value = _load(data, {"schema", "items", "digest", "byte_count"}, _PACK)
        pack = cls.build(_decode(value["items"]))
        if (
            type(value["byte_count"]) is not int
            or value["byte_count"] != pack.byte_count
            or value["digest"] != pack.digest
        ):
            raise ContextPackError("Pack digest or byte count mismatch")  # noqa: TRY003
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self):
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("Invalid prefix count")  # noqa: TRY003
        _items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack):
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self):
        return len(self.to_bytes())

    def to_bytes(self):
        return _json({
            "schema": _DELTA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode(self.appended),
        })

    @classmethod
    def from_bytes(cls, data):
        value = _load(data, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}, _DELTA)
        return cls(value["base_digest"], value["target_digest"], value["prefix_items"], _decode(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if base != ContextPack.build(base.items) or base.digest != delta.base_digest:
        raise ContextPackError("Base pack mismatch")  # noqa: TRY003
    if type(delta.prefix_items) is not int or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("Prefix outside base pack")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("Target digest mismatch")  # noqa: TRY003
    return target
