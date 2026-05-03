"""Seed ``ranked_pools`` from nika.bot's curated ``Autolobby maps`` JSON files.

Usage:

    # Use the bundled fallback fixtures (no extra clones required):
    python scripts/seed_ranked_pools.py

    # Or point it at a real nika.bot checkout:
    python scripts/seed_ranked_pools.py --source /path/to/nika.bot/src/data/Autolobby\\ maps

The seed assigns ~3 hashes per slot per tier-bucket (Silver / Gold / Diamond)
by partitioning each input pool into thirds. That's enough for end-to-end
ranked tests and gives operators a starting point they can override later.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from objects import glob  # noqa: E402
from objects.ranked import db as ranked_db  # noqa: E402

SLOTS = ["NM1", "NM2", "NM3", "HD1", "HD2", "HR1", "HR2", "DT1", "DT2", "TB"]
TIERS = ["Silver", "Gold", "Diamond"]
PER_TIER_PER_SLOT = 3


def _load_slot_hashes(source_dir: Path, slot: str) -> List[str]:
    """Read a nika.bot autolobby JSON file and return its hashes (or [])."""
    path = source_dir / f"{slot}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    hashes = data.get("hashes") or []
    return [str(h) for h in hashes if h]


def _split_for_tiers(hashes: List[str], n_tiers: int) -> List[List[str]]:
    """Split a flat list of hashes into ``n_tiers`` evenly-sized chunks."""
    if not hashes:
        return [[] for _ in range(n_tiers)]
    chunk = max(1, len(hashes) // n_tiers)
    chunks: List[List[str]] = []
    for i in range(n_tiers):
        start = i * chunk
        end = start + chunk if i < n_tiers - 1 else len(hashes)
        chunks.append(hashes[start:end])
    return chunks


def _select(chunk: List[str], k: int) -> List[str]:
    if len(chunk) <= k:
        return chunk
    step = max(1, len(chunk) // k)
    return [chunk[i] for i in range(0, len(chunk), step)][:k]


async def _seed(slot_to_tier_hashes: Dict[str, Dict[str, Iterable[str]]]) -> int:
    inserted = 0
    for slot, by_tier in slot_to_tier_hashes.items():
        for tier, hashes in by_tier.items():
            for md5 in hashes:
                await ranked_db.upsert_pool_entry(
                    tier_bucket=tier,
                    slot=slot,
                    beatmap_md5=md5,
                )
                inserted += 1
    return inserted


def _build_plan(source: Path) -> Dict[str, Dict[str, List[str]]]:
    plan: Dict[str, Dict[str, List[str]]] = {}
    for slot in SLOTS:
        all_hashes = _load_slot_hashes(source, slot)
        if not all_hashes:
            continue
        if slot.upper() == "TB":
            tb_chunk = _select(all_hashes, 3)
            plan[slot] = {tier: tb_chunk for tier in TIERS}
            continue
        chunks = _split_for_tiers(all_hashes, len(TIERS))
        plan[slot] = {
            tier: _select(chunk, PER_TIER_PER_SLOT)
            for tier, chunk in zip(TIERS, chunks)
        }
    return plan


async def main_async(source: Path) -> None:
    if not source.exists():
        print(f"!! source not found: {source}", file=sys.stderr)
        sys.exit(2)
    await glob.db.connect()
    try:
        plan = _build_plan(source)
        if not plan:
            print("!! no pool files found in", source, file=sys.stderr)
            sys.exit(2)
        inserted = await _seed(plan)
        for slot, by_tier in plan.items():
            for tier, hashes in by_tier.items():
                print(f"  {tier:<8} {slot:<4} {len(list(hashes))} maps")
        print(f"Seeded {inserted} pool entries from {source}")
    finally:
        await glob.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=os.environ.get(
            "RANKED_POOL_SOURCE",
            str(REPO_ROOT / "data" / "fixtures" / "ranked_pools"),
        ),
        help="Directory with NM1.json / HD1.json / ... files (default: bundled fixtures).",
    )
    args = parser.parse_args()
    asyncio.run(main_async(Path(args.source)))


if __name__ == "__main__":
    main()
