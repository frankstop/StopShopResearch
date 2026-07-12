import json
import unittest
from pathlib import Path

from stopshop_research.storage import read_jsonl_gz, snapshot_paths


class PublishedCatalogHistoryTests(unittest.TestCase):
    def test_every_raw_item_and_observation_is_reachable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        output = root / "docs" / "data" / "catalog-history"
        manifest = json.loads((output / "manifest.json").read_text())
        index_payload = json.loads((output / "catalog-index.json").read_text())
        index = [dict(zip(index_payload["item_fields"], row)) for row in index_payload["items"]]
        index_by_id = {row["id"]: row for row in index}

        raw_ids = set()
        raw_observations = 0
        for path in snapshot_paths(root / "data" / "snapshots", "catalog"):
            snapshot_ids = {str(row["product_key"]) for row in read_jsonl_gz(path) if row.get("product_key")}
            raw_ids.update(snapshot_ids)
            raw_observations += len(snapshot_ids)

        shard_items = {}
        for path in sorted((output / "items").glob("*.json")):
            shard_items.update(json.loads(path.read_text())["items"])

        self.assertEqual(set(index_by_id), raw_ids)
        self.assertEqual(set(shard_items), raw_ids)
        self.assertEqual(manifest["unique_items"], len(raw_ids))
        self.assertEqual(sum(len(rows) for rows in shard_items.values()), raw_observations)
        for item_id, rows in shard_items.items():
            self.assertEqual(index_by_id[item_id]["observations"], len(rows))


if __name__ == "__main__":
    unittest.main()
