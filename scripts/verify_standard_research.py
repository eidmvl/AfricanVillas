"""Manual end-to-end check of one real jurisdiction research."""

from __future__ import annotations

import argparse
import asyncio
import time

from african_villas.analysis import AsyncCodexAnalyzer
from african_villas.database import Repository


async def run(country: str, region: str, goal: str, save_cache: bool) -> None:
    started = time.perf_counter()

    def status(_code: str, message: str) -> None:
        print(message, flush=True)

    async with AsyncCodexAnalyzer() as analyzer:
        research = await analyzer.analyze_jurisdiction(
            country, region, [goal], "standard", status
        )
    if save_cache:
        Repository().save_jurisdiction_research(research)
    counts = {
        "land": len(research.land_rights.sources),
        "entity": len(research.recommended_entity.sources),
        "capital": len(research.capital_requirements.sources),
        "foreign": len(research.foreign_company_rules.sources),
        "local_rules": len(research.local_rules.sources),
    }
    print(f"Research OK in {time.perf_counter() - started:.1f} s: {counts}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", default="Танзания")
    parser.add_argument("--region", default="Zanzibar")
    parser.add_argument("--goal", default="Строительство вилл для продажи")
    parser.add_argument("--save-cache", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.country, args.region, args.goal, args.save_cache))


if __name__ == "__main__":
    main()
