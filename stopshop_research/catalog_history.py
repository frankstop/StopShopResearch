from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .storage import read_jsonl_gz, snapshot_paths


SCHEMA_VERSION = "1.0"
SHARD_COUNT = 64


def _date_from_path(path: Path) -> str:
    return path.name.split(".", 1)[0]


def _price(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 2) if parsed > 0 else None


def _shard(item_id: str) -> str:
    return f"{int(sha256(item_id.encode('utf-8')).hexdigest()[:2], 16) % SHARD_COUNT:02x}"


def _write_compact(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_catalog_history(snapshot_dir: Path, output_dir: Path) -> dict[str, Any]:
    paths = snapshot_paths(snapshot_dir, "catalog")
    if not paths:
        raise ValueError("No catalog snapshots found")

    dates = [_date_from_path(path) for path in paths]
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metadata: dict[str, dict[str, Any]] = {}

    for date, path in zip(dates, paths):
        keyed = {
            str(row["product_key"]): row
            for row in read_jsonl_gz(path)
            if row.get("product_key") and _price(row.get("current_price")) is not None
        }
        for item_id, row in keyed.items():
            categories = sorted({str(value) for value in row.get("categories", []) if value})
            metadata[item_id] = {
                "id": item_id,
                "name": str(row.get("name") or "Unknown product"),
                "brand": row.get("brand"),
                "categories": categories,
            }
            by_item[item_id].append(
                {
                    "date": date,
                    "price": _price(row.get("current_price")),
                    "regular_price": None,
                    "availability": row.get("availability"),
                    "categories": categories,
                }
            )

    latest_date = dates[-1]
    index_items: list[dict[str, Any]] = []
    shard_items: dict[str, dict[str, Any]] = defaultdict(dict)
    category_counts: dict[str, int] = defaultdict(int)
    current_items = 0

    for item_id, observations in by_item.items():
        meta = metadata[item_id]
        observed_dates = {row["date"] for row in observations}
        first_seen = observations[0]["date"]
        last_seen = observations[-1]["date"]
        is_current = last_seen == latest_date
        has_gap = any(date not in observed_dates for date in dates[dates.index(first_seen) :])
        if is_current:
            current_items += 1
            status = "new" if len(dates) > 1 and first_seen == latest_date else "returned" if has_gap else "current"
        else:
            status = "missing"

        prices = [row["price"] for row in observations if row["price"] is not None]
        aligned = {row["date"]: row["price"] for row in observations}
        trend = [aligned.get(date) for date in dates]
        change = None
        if len(dates) >= 2 and dates[-2] in aligned and latest_date in aligned:
            change = round(aligned[latest_date] - aligned[dates[-2]], 2)

        for category in meta["categories"]:
            category_counts[category] += 1

        shard = _shard(item_id)
        summary = {
            **meta,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "observations": len(observations),
            "status": status,
            "current_price": observations[-1]["price"] if is_current else None,
            "latest_change": change,
            "minimum_price": min(prices),
            "maximum_price": max(prices),
            "trend": trend,
            "shard": shard,
        }
        index_items.append(summary)
        shard_items[shard][item_id] = [
            [row["date"], row["price"], row["regular_price"], row["availability"], row["categories"]]
            for row in observations
        ]

    index_items.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    items_dir = output_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    written_shards = set()
    shard_manifest = []
    for shard, items in sorted(shard_items.items()):
        filename = f"{shard}.json"
        _write_compact(
            items_dir / filename,
            {
                "schema_version": SCHEMA_VERSION,
                "observation_fields": ["date", "price", "regular_price", "availability", "categories"],
                "items": items,
            },
        )
        written_shards.add(filename)
        shard_manifest.append({"id": shard, "items": len(items), "path": f"items/{filename}"})
    for stale in items_dir.glob("*.json"):
        if stale.name not in written_shards:
            stale.unlink()

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "retailer": "Stop & Shop",
        "identity_field": "Canonical product URL",
        "snapshot_dates": dates,
        "snapshot_count": len(dates),
        "date_start": dates[0],
        "date_end": latest_date,
        "unique_items": len(index_items),
        "current_items": current_items,
        "missing_items": len(index_items) - current_items,
        "categories": [
            {"name": name, "items": count}
            for name, count in sorted(category_counts.items(), key=lambda row: (-row[1], row[0].casefold()))
        ],
        "shards": shard_manifest,
    }
    _write_compact(output_dir / "manifest.json", manifest)
    item_fields = [
        "id", "name", "brand", "categories", "first_seen", "last_seen",
        "observations", "status", "current_price", "latest_change",
        "minimum_price", "maximum_price", "trend", "shard",
    ]
    _write_compact(
        output_dir / "catalog-index.json",
        {
            "schema_version": SCHEMA_VERSION,
            "item_fields": item_fields,
            "items": [[item[field] for field in item_fields] for item in index_items],
        },
    )
    return manifest
