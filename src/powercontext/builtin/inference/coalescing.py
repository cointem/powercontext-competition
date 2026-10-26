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

"""Share in-flight embedding work and retain successful vectors in an LRU."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(frozen=True)
class _CompletedVector:
    vector: EmbeddingVector
    batch: object
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    def __init__(
        self, model: EmbeddingModel, *, max_batch_size: int = 64, coalesce_ms: float = 5.0, cache_size: int = 1024
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        if not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be finite and nonnegative")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_CompletedVector]] = {}
        self._pending: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vectors = {}
        waiting = {}
        loop = asyncio.get_running_loop()
        for text in texts:
            if text in vectors or text in waiting:
                continue
            if text in self._cache:
                vectors[text] = self._cache[text]
                self._cache.move_to_end(text)
            else:
                if text not in self._inflight:
                    future = loop.create_future()
                    # Retrieve failures even when every caller has cancelled its wait.
                    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                    self._inflight[text] = future
                    self._pending.append(text)
                waiting[text] = self._inflight[text]
        if self._pending and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush())
        results = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
        usages = {}
        for text, result in zip(waiting, results, strict=True):
            vectors[text] = result.vector
            usages[result.batch] = result.usage
        usage = InferenceUsage(
            requests=sum(item.requests for item in usages.values()),
            input_tokens=_sum_tokens(usages.values(), "input_tokens"),
            output_tokens=_sum_tokens(usages.values(), "output_tokens"),
        )
        return EmbeddingResult(vectors=tuple(vectors[text] for text in texts), usage=usage)

    async def _flush(self) -> None:
        await asyncio.sleep(self._delay)
        pending, self._pending = self._pending, []
        self._flush_task = None
        for start in range(0, len(pending), self._max_batch_size):
            task = asyncio.create_task(self._run_batch(tuple(pending[start : start + self._max_batch_size])))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, texts: tuple[str, ...]) -> None:
        batch = object()
        try:
            result = await self._model.embed(texts)
            _validate_count(result, len(texts))
        except BaseException as error:
            for text in texts:
                self._inflight.pop(text).set_exception(error)
            return
        for text, vector in zip(texts, result.vectors, strict=True):
            if self._cache_size:
                self._cache[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            self._inflight.pop(text).set_result(_CompletedVector(vector, batch, result.usage))


def _sum_tokens(usages, field):
    values = [getattr(usage, field) for usage in usages if getattr(usage, field) is not None]
    return sum(values) if values else None


def _validate_count(result: EmbeddingResult, expected: int) -> None:
    if len(result.vectors) != expected:
        raise ValueError("Embedding provider returned the wrong vector count")  # noqa: TRY003
