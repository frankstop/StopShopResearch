import tempfile
import unittest
from pathlib import Path

from stopshop_research.catalog_history import build_catalog_history
from stopshop_research.models import ProductObservation
from stopshop_research.storage import write_jsonl_gz_atomic


def product(item_id: str, name: str, price: float, observed_at: str) -> ProductObservation:
    return ProductObservation(
        product_key=f"https://example.com/{item_id}",
        name=name,
        brand="Test Brand",
        current_price=price,
        currency="USD",
        availability="InStock",
        categories=["Test"],
        source_url=f"https://example.com/{item_id}",
        image_url=None,
        description=None,
        package_text="1 ct",
        price_valid_until=None,
        observed_at=observed_at,
    )


class CatalogHistoryTests(unittest.TestCase):
    def test_builds_union_catalog_with_gaps_and_current_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots = root / "snapshots"
            write_jsonl_gz_atomic(
                snapshots / "2026-07-01.catalog.jsonl.gz",
                [product("apple", "Apple", 1.0, "2026-07-01T00:00:00Z"), product("milk", "Milk", 3.0, "2026-07-01T00:00:00Z")],
            )
            write_jsonl_gz_atomic(
                snapshots / "2026-07-08.catalog.jsonl.gz",
                [product("milk", "Milk", 3.5, "2026-07-08T00:00:00Z")],
            )
            write_jsonl_gz_atomic(
                snapshots / "2026-07-15.catalog.jsonl.gz",
                [product("apple", "Apple", 1.25, "2026-07-15T00:00:00Z"), product("bread", "Bread", 2.0, "2026-07-15T00:00:00Z")],
            )

            output = root / "public"
            manifest = build_catalog_history(snapshots, output)
            self.assertEqual(manifest["unique_items"], 3)
            self.assertEqual(manifest["current_items"], 2)
            self.assertEqual(manifest["missing_items"], 1)

            import json

            payload = json.loads((output / "catalog-index.json").read_text())
            index = [dict(zip(payload["item_fields"], row)) for row in payload["items"]]
            by_name = {row["name"]: row for row in index}
            self.assertEqual(by_name["Apple"]["status"], "returned")
            self.assertEqual(by_name["Apple"]["trend"], [1.0, None, 1.25])
            self.assertEqual(by_name["Milk"]["status"], "missing")
            self.assertEqual(by_name["Bread"]["status"], "new")
            self.assertTrue((output / "items" / f'{by_name["Apple"]["shard"]}.json').exists())


if __name__ == "__main__":
    unittest.main()
