import unittest
from pathlib import Path

from stopshop_research.parsers import (
    canonical_url,
    merge_products,
    parse_catalog_page,
    parse_promotions_page,
    parse_sitemap,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_sitemap_and_canonical_url(self) -> None:
        xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>HTTPS://StopAndShop.com/groceries/milk.html?x=1</loc></url></urlset>'
        self.assertEqual(parse_sitemap(xml), ["https://stopandshop.com/groceries/milk.html"])
        self.assertEqual(canonical_url("https://EXAMPLE.com/a//b/?x=1#top"), "https://example.com/a/b")

    def test_catalog_json_ld_extracts_and_cleans_fields(self) -> None:
        html = (FIXTURES / "category.html").read_text()
        rows = parse_catalog_page(html, "https://stopandshop.com/groceries/dairy-eggs/milk.html", "2026-07-09T00:00:00Z")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "Whole Milk & Cream")
        self.assertEqual(row.brand, "Acme & Co")
        self.assertEqual(row.current_price, 4.29)
        self.assertEqual(row.product_key, "https://stopandshop.com/groceries/dairy-eggs/milk/acme-whole-milk-1-gallon.html")
        self.assertEqual(row.categories, ["Milk"])
        self.assertEqual(row.price_scope, "public_online_catalog")
        self.assertIn("not asserted", row.market_reference["note"])

    def test_duplicate_products_merge_categories(self) -> None:
        html = (FIXTURES / "category.html").read_text()
        first = parse_catalog_page(html, "https://stopandshop.com/groceries/dairy-eggs/milk.html", "2026-07-09T00:00:00Z")[0]
        second = parse_catalog_page(html, "https://stopandshop.com/groceries/dairy-eggs/featured.html", "2026-07-09T00:00:00Z")[0]
        merged = merge_products(first, second)
        self.assertEqual(merged.categories, ["Featured", "Milk"])

    def test_missing_brand_and_malformed_json_are_safe(self) -> None:
        html = '<script type="application/ld+json">not-json</script><script type="application/ld+json">{"@type":"Product","name":"Bread","url":"https://stopandshop.com/groceries/bread.html","offers":{"price":"2.99"}}</script>'
        rows = parse_catalog_page(html, "https://stopandshop.com/groceries/bread-bakery.html", "now")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].brand)

    def test_promotion_parser_ignores_non_sale_and_reads_discount(self) -> None:
        rows = parse_promotions_page((FIXTURES / "promotions.html").read_text(), "https://stopandshop.com/savings/weekly-ad", "now")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Nature's Promise Ground Beef")
        self.assertEqual(rows[0].advertised_price, 7.87)
        self.assertEqual(rows[0].regular_price, 10.49)
        self.assertEqual(rows[0].unit_price_text, "16 OZ PKG | $7.87 /LB")


if __name__ == "__main__":
    unittest.main()
