"""Manual connectivity check for the four-worker AsyncCodex pool."""

from __future__ import annotations

import asyncio
import json
import time

from openai_codex import AsyncCodex, Sandbox
from openai_codex.types import ReasoningEffort


SCHEMA = {
    "type": "object",
    "properties": {
        "worker": {"type": "integer"},
        "ok": {"type": "boolean"},
    },
    "required": ["worker", "ok"],
    "additionalProperties": False,
}


async def main() -> None:
    started = time.perf_counter()
    async with AsyncCodex() as codex:
        threads = await asyncio.gather(
            *(
                codex.thread_start(model="gpt-5.6-luna", sandbox=Sandbox.read_only)
                for _ in range(4)
            )
        )
        responses = await asyncio.gather(
            *(
                thread.run(
                    f"Проверка соединения. Верни worker={number} и ok=true.",
                    effort=ReasoningEffort.high,
                    output_schema=SCHEMA,
                )
                for number, thread in enumerate(threads, start=1)
            )
        )
    payloads = [json.loads(str(response.final_response)) for response in responses]
    assert sorted(payload["worker"] for payload in payloads) == [1, 2, 3, 4]
    assert all(payload["ok"] is True for payload in payloads)
    print(f"AsyncCodex smoke OK: 4/4, {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    asyncio.run(main())
