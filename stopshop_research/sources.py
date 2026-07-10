from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import logging
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from .models import ProductObservation, PromotionObservation
from .parsers import merge_products, parse_catalog_page, parse_promotions_page, parse_sitemap


LOGGER = logging.getLogger(__name__)
BASE_URL = "https://stopandshop.com"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
SITEMAP_URL = f"{BASE_URL}/groceries/sitemap.xml"
WEEKLY_AD_URL = f"{BASE_URL}/savings/weekly-ad"
USER_AGENT = "StopShopResearch/1.0 (+https://github.com/frankstop/StopShopResearch)"


class CollectionError(RuntimeError):
    pass


@dataclass
class SourceInventory:
    category_urls: list[str]
    product_urls: set[str]


class PoliteClient:
    def __init__(
        self,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.delay_seconds = max(delay_seconds, 0.0)
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.opener = opener
        self.requests = 0
        self._last_request_at = 0.0

    def fetch_bytes(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.delay_seconds:
                time.sleep(self.delay_seconds - elapsed)
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xml,text/xml;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "gzip",
                },
            )
            try:
                self.requests += 1
                self._last_request_at = time.monotonic()
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    payload = response.read()
                    encoding = response.headers.get("Content-Encoding", "")
                    if encoding == "gzip" or payload.startswith(b"\x1f\x8b"):
                        payload = gzip.decompress(payload)
                    return payload
            except HTTPError as error:
                last_error = error
                if error.code != 429 and error.code < 500:
                    break
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
            if attempt < self.retries:
                time.sleep(2**attempt)
        raise CollectionError(f"Failed to fetch {url}: {last_error}")

    def fetch_text(self, url: str) -> str:
        return self.fetch_bytes(url).decode("utf-8", errors="replace")


def verify_robots(client: PoliteClient) -> str:
    robots_text = client.fetch_text(ROBOTS_URL)
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)
    parser.parse(robots_text.splitlines())
    if not parser.can_fetch(USER_AGENT, f"{BASE_URL}/groceries/dairy-eggs.html"):
        raise CollectionError("robots.txt does not allow catalog collection under /groceries/")
    forbidden = ["/api/", "/product-search/", "/browse-aisles/"]
    if any(path in SITEMAP_URL for path in forbidden):
        raise CollectionError("Configured source path violates the collection boundary")
    return robots_text


def discover_sources(client: PoliteClient) -> SourceInventory:
    index_urls = parse_sitemap(client.fetch_text(SITEMAP_URL))
    category_sitemap = next((url for url in index_urls if "categories-sitemap" in url), None)
    product_sitemap = next((url for url in index_urls if "products-sitemap" in url), None)
    if not category_sitemap or not product_sitemap:
        raise CollectionError("The grocery sitemap index did not expose category and product sitemaps")
    category_urls = [
        url
        for url in parse_sitemap(client.fetch_text(category_sitemap))
        if url.startswith(f"{BASE_URL}/groceries/") and not url.endswith("/index.html")
    ]
    product_urls = {
        url
        for url in parse_sitemap(client.fetch_text(product_sitemap))
        if url.startswith(f"{BASE_URL}/groceries/")
    }
    if not category_urls or not product_urls:
        raise CollectionError("Sitemap discovery returned no usable catalog URLs")
    return SourceInventory(category_urls=sorted(set(category_urls)), product_urls=product_urls)


def collect_catalog(
    client: PoliteClient,
    inventory: SourceInventory,
    observed_at: str | None = None,
    max_categories: int | None = None,
) -> tuple[list[ProductObservation], list[str]]:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    category_urls = inventory.category_urls[:max_categories] if max_categories else inventory.category_urls
    products: dict[str, ProductObservation] = {}
    errors: list[str] = []
    for index, category_url in enumerate(category_urls, 1):
        try:
            parsed = parse_catalog_page(client.fetch_text(category_url), category_url, observed_at)
            for product in parsed:
                existing = products.get(product.product_key)
                products[product.product_key] = merge_products(existing, product) if existing else product
        except (CollectionError, ValueError) as error:
            if len(errors) < 100:
                errors.append(f"{category_url}: {error}")
        if index == 1 or index % 100 == 0 or index == len(category_urls):
            LOGGER.info("Catalog progress %s/%s: %s unique products", index, len(category_urls), len(products))
    return sorted(products.values(), key=lambda item: item.product_key), errors


def collect_promotions(
    client: PoliteClient,
    observed_at: str | None = None,
) -> tuple[list[PromotionObservation], list[str]]:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        html = client.fetch_text(WEEKLY_AD_URL)
        return parse_promotions_page(html, WEEKLY_AD_URL, observed_at), []
    except (CollectionError, ValueError) as error:
        return [], [f"{WEEKLY_AD_URL}: {error}"]
