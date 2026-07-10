from __future__ import annotations

import hashlib
import html as html_module
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from .models import ProductObservation, PromotionObservation


def canonical_url(url: str) -> str:
    parts = urlsplit(html_module.unescape(url.strip()))
    path = re.sub(r"/{2,}", "/", parts.path)
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def parse_sitemap(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "loc" and node.text:
            urls.append(canonical_url(node.text))
    return urls


class _StructuredDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_json_ld = False
        self.buffer: list[str] = []
        self.blocks: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "script" and (attr_map.get("type") or "").lower() == "application/ld+json":
            self.in_json_ld = True
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_json_ld:
            self.blocks.append("".join(self.buffer).strip())
            self.in_json_ld = False
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.in_json_ld:
            self.buffer.append(data)
        elif data.strip():
            self.text_parts.append(html_module.unescape(data.strip()))


def _json_blocks(page_html: str) -> tuple[list[Any], list[str]]:
    parser = _StructuredDataParser()
    parser.feed(page_html)
    decoded: list[Any] = []
    for raw in parser.blocks:
        try:
            decoded.append(json.loads(html_module.unescape(raw)))
        except json.JSONDecodeError:
            continue
    return decoded, parser.text_parts


def _walk_products(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        node_type = value.get("@type")
        if node_type == "Product":
            yield value
        if node_type == "ListItem" and isinstance(value.get("item"), dict):
            yield from _walk_products(value["item"])
        for key, child in value.items():
            if key != "item":
                yield from _walk_products(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_products(child)


def _category_from_page(page_url: str) -> str:
    path = urlsplit(page_url).path
    parts = [part for part in path.split("/") if part]
    if "groceries" not in parts:
        return "Uncategorized"
    relative = parts[parts.index("groceries") + 1 :]
    if relative and relative[-1].endswith(".html"):
        relative[-1] = relative[-1][:-5]
    leaf = relative[-1] if relative else "uncategorized"
    return " ".join(word.capitalize() for word in leaf.split("-"))


def _package_hint(product_url: str, name: str) -> str | None:
    stem = urlsplit(product_url).path.rsplit("/", 1)[-1].removesuffix(".html")
    name_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if stem.startswith(name_slug + "-"):
        suffix = stem[len(name_slug) + 1 :]
        if re.search(r"\d", suffix):
            return suffix.replace("-", " ")
    return None


def _price(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return round(parsed, 2) if parsed > 0 else None


def parse_catalog_page(page_html: str, page_url: str, observed_at: str) -> list[ProductObservation]:
    blocks, _ = _json_blocks(page_html)
    category = _category_from_page(page_url)
    products: list[ProductObservation] = []
    seen: set[str] = set()
    for block in blocks:
        for raw in _walk_products(block):
            offers = raw.get("offers") if isinstance(raw.get("offers"), dict) else {}
            product_url = canonical_url(str(raw.get("url") or offers.get("url") or ""))
            name = html_module.unescape(str(raw.get("name") or "")).strip()
            price = _price(offers.get("price"))
            if not product_url or not name or price is None or product_url in seen:
                continue
            seen.add(product_url)
            brand_value = raw.get("brand")
            if isinstance(brand_value, dict):
                brand = html_module.unescape(str(brand_value.get("name") or "")).strip() or None
            else:
                brand = html_module.unescape(str(brand_value or "")).strip() or None
            products.append(
                ProductObservation(
                    product_key=product_url,
                    name=name,
                    brand=brand,
                    current_price=price,
                    currency=str(offers.get("priceCurrency") or "USD"),
                    availability=str(offers.get("availability") or "").rsplit("/", 1)[-1] or None,
                    categories=[category],
                    source_url=product_url,
                    image_url=str(raw.get("image") or "").strip() or None,
                    description=html_module.unescape(str(raw.get("description") or "")).strip() or None,
                    package_text=_package_hint(product_url, name),
                    price_valid_until=str(offers.get("priceValidUntil") or "").strip() or None,
                    observed_at=observed_at,
                )
            )
    return products


def merge_products(existing: ProductObservation, incoming: ProductObservation) -> ProductObservation:
    categories = sorted(set(existing.categories) | set(incoming.categories))
    values = existing.to_dict()
    values["categories"] = categories
    if not values.get("brand") and incoming.brand:
        values["brand"] = incoming.brand
    if not values.get("package_text") and incoming.package_text:
        values["package_text"] = incoming.package_text
    return ProductObservation(**{key: value for key, value in values.items() if key != "schema_version"})


_SALE_PATTERN = re.compile(r"^(?P<name>.+?)\s+Sale Price\s*\$(?P<sale>\d+(?:\.\d{1,2})?)$", re.I)
_REGULAR_PATTERN = re.compile(r"^Original Price\s*\$(?P<regular>\d+(?:\.\d{1,2})?)$", re.I)
_UNIT_PATTERN = re.compile(r"^(?P<size>.+?)\s*\|\s*(?P<unit>\$[^|]+)$")


def parse_promotions_page(page_html: str, page_url: str, observed_at: str) -> list[PromotionObservation]:
    blocks, text_parts = _json_blocks(page_html)
    promotions: dict[str, PromotionObservation] = {}

    for block in blocks:
        for raw in _walk_products(block):
            offers = raw.get("offers") if isinstance(raw.get("offers"), dict) else {}
            sale = _price(offers.get("price"))
            regular = _price(offers.get("highPrice") or offers.get("regularPrice"))
            name = html_module.unescape(str(raw.get("name") or "")).strip()
            if not name or sale is None or regular is None or sale >= regular:
                continue
            product_url = canonical_url(str(raw.get("url") or offers.get("url") or "")) or None
            key_material = f"{name.lower()}|{sale}|{regular}|{offers.get('priceValidUntil', '')}"
            key = hashlib.sha256(key_material.encode()).hexdigest()[:20]
            promotions[key] = PromotionObservation(
                promotion_key=key,
                product_key=product_url,
                name=name,
                advertised_price=sale,
                regular_price=regular,
                unit_price_text=None,
                offer_text=None,
                valid_from=None,
                valid_to=str(offers.get("priceValidUntil") or "") or None,
                source_url=page_url,
                observed_at=observed_at,
            )

    for index, text in enumerate(text_parts):
        sale_match = _SALE_PATTERN.match(text)
        if not sale_match:
            continue
        name = sale_match.group("name").strip()
        sale = float(sale_match.group("sale"))
        regular: float | None = None
        unit_text: str | None = None
        for following in text_parts[index + 1 : index + 5]:
            regular_match = _REGULAR_PATTERN.match(following)
            if regular_match:
                regular = float(regular_match.group("regular"))
            unit_match = _UNIT_PATTERN.match(following)
            if unit_match:
                unit_text = f"{unit_match.group('size')} | {unit_match.group('unit')}"
        key_material = f"{name.lower()}|{sale}|{regular or ''}|{unit_text or ''}"
        key = hashlib.sha256(key_material.encode()).hexdigest()[:20]
        promotions.setdefault(
            key,
            PromotionObservation(
                promotion_key=key,
                name=name,
                advertised_price=sale,
                regular_price=regular,
                unit_price_text=unit_text,
                offer_text=None,
                valid_from=None,
                valid_to=None,
                source_url=page_url,
                observed_at=observed_at,
            ),
        )
    return list(promotions.values())
