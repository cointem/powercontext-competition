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

"""HTTP scoring with global, identity-preserving stable selection."""

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference.models import InferenceUsage


class HTTPMemoryReranker:
    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        if not candidates or type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("limit must address a nonempty candidate pool")  # noqa: TRY003
        scores = {}
        requests = 0
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            expected = set(range(start + 1, start + len(batch) + 1))
            response = await self.client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": start + i + 1, "text": candidate.text} for i, candidate in enumerate(batch)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
                raise ValueError("Invalid reranking response")  # noqa: TRY003
            seen = set()
            for item in payload["scores"]:
                if not isinstance(item, dict) or set(item) != {"rank", "score"}:
                    raise ValueError("Invalid score record")  # noqa: TRY003
                rank, score = item["rank"], item["score"]
                if type(rank) is not int or rank not in expected or rank in seen:
                    raise ValueError("Invalid or repeated score rank")  # noqa: TRY003
                if type(score) not in (int, float) or not math.isfinite(score):
                    raise ValueError("Score must be finite")  # noqa: TRY003
                seen.add(rank)
                scores[rank] = score
            if seen != expected:
                raise ValueError("Missing score ranks")  # noqa: TRY003
        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(selected_ranks=selected, usage=InferenceUsage(requests=requests))
