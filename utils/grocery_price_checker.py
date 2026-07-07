#!/usr/bin/env python3
"""
Grocery Price Checker — Kroger, Meijer, ALDI
Locations: Zionsville, IN (46077) and Whitestown, IN (46075)

Usage:
    python grocery_price_checker.py "milk, eggs, bread, butter"
    python grocery_price_checker.py milk eggs bread butter

Setup:
    pip install requests playwright tabulate
    playwright install chromium

    Kroger API (free): https://developer.kroger.com
    Set env vars:  KROGER_CLIENT_ID  and  KROGER_CLIENT_SECRET
"""

import asyncio
import base64
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

try:
    from playwright.async_api import async_playwright, Page
except ImportError:
    print("ERROR: playwright not installed. Run:  pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


# ─── Configuration ────────────────────────────────────────────────────────────

KROGER_CLIENT_ID     = os.getenv("KROGER_CLIENT_ID", "")
KROGER_CLIENT_SECRET = os.getenv("KROGER_CLIENT_SECRET", "")

LOCATIONS = {
    "Zionsville": "46077",
    "Whitestown": "46075",
}

# How many top results to show per product per store/location
TOP_N = 1


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class PriceResult:
    store: str
    location: str
    product_query: str
    product_name: str
    price: Optional[float]
    size: str = ""
    error: str = ""

    @property
    def price_str(self) -> str:
        if self.price is not None:
            return f"${self.price:.2f}"
        return self.error or "N/A"


# ─── Kroger API ───────────────────────────────────────────────────────────────

class KrogerAPI:
    _BASE      = "https://api.kroger.com/v1"
    _TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"

    def __init__(self, client_id: str, client_secret: str):
        self._id     = client_id
        self._secret = client_secret
        self._token: Optional[str] = None
        self._expiry: float = 0

    def _token_header(self) -> dict:
        if not self._token or time.time() >= self._expiry:
            creds = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
            r = requests.post(
                self._TOKEN_URL,
                headers={"Authorization": f"Basic {creds}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": "product.compact"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            self._token  = data["access_token"]
            self._expiry = time.time() + data["expires_in"] - 60
        return {"Authorization": f"Bearer {self._token}"}

    def nearest_location_id(self, zip_code: str) -> Optional[str]:
        r = requests.get(
            f"{self._BASE}/locations",
            headers=self._token_header(),
            params={"filter.zipCode.near": zip_code,
                    "filter.radiusInMiles": "20",
                    "filter.chain": "kroger",
                    "filter.limit": "1"},
            timeout=15,
        )
        r.raise_for_status()
        locs = r.json().get("data", [])
        return locs[0]["locationId"] if locs else None

    def search(self, query: str, location_id: str) -> list[dict]:
        r = requests.get(
            f"{self._BASE}/products",
            headers=self._token_header(),
            params={"filter.term": query,
                    "filter.locationId": location_id,
                    "filter.limit": str(TOP_N),
                    "filter.fulfillment": "ais"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("data", [])


def check_kroger(products: list[str]) -> list[PriceResult]:
    results: list[PriceResult] = []

    if not KROGER_CLIENT_ID or not KROGER_CLIENT_SECRET:
        for loc in LOCATIONS:
            for p in products:
                results.append(PriceResult("Kroger", loc, p, "", None,
                    error="Set KROGER_CLIENT_ID and KROGER_CLIENT_SECRET env vars"))
        return results

    api = KrogerAPI(KROGER_CLIENT_ID, KROGER_CLIENT_SECRET)

    for loc_name, zip_code in LOCATIONS.items():
        print(f"  Kroger [{loc_name}]: finding store near {zip_code}...")
        try:
            loc_id = api.nearest_location_id(zip_code)
        except Exception as e:
            for p in products:
                results.append(PriceResult("Kroger", loc_name, p, "", None, error=str(e)))
            continue

        if not loc_id:
            for p in products:
                results.append(PriceResult("Kroger", loc_name, p, "", None, error="No Kroger store found"))
            continue

        for product in products:
            print(f"    {product}")
            try:
                items = api.search(product, loc_id)
                if not items:
                    results.append(PriceResult("Kroger", loc_name, product, "Not found", None))
                else:
                    item       = items[0]
                    name       = item.get("description", "")
                    item_info  = item.get("items", [{}])[0]
                    size       = item_info.get("size", "")
                    price_info = item_info.get("price", {})
                    price      = price_info.get("promo") or price_info.get("regular")
                    results.append(PriceResult("Kroger", loc_name, product, name,
                                               float(price) if price else None, size))
                time.sleep(0.25)
            except Exception as e:
                results.append(PriceResult("Kroger", loc_name, product, "", None, error=str(e)))

    return results


# ─── Meijer (Playwright) ──────────────────────────────────────────────────────

async def _meijer_search_page(page: Page, product: str) -> tuple[str, Optional[float], str]:
    """Return (name, price, size) for best Meijer result."""
    await page.goto(
        f"https://www.meijer.com/search.html#{product.replace(' ', '+')}",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    # Wait for product tiles to appear
    try:
        await page.wait_for_selector(
            "[class*='ProductCard'], [class*='product-card'], [data-testid*='product']",
            timeout=15_000,
        )
    except Exception:
        return "Not found", None, ""

    cards = await page.query_selector_all(
        "[class*='ProductCard'], [class*='product-card'], [data-testid*='product']"
    )
    if not cards:
        return "Not found", None, ""

    card = cards[0]

    name_el  = await card.query_selector("[class*='ProductTitle'], [class*='product-title'], [class*='name'], h3, h2, h4")
    price_el = await card.query_selector("[class*='Price'], [class*='price'], [data-testid*='price']")
    size_el  = await card.query_selector("[class*='Size'], [class*='size'], [class*='unit'], [class*='weight']")

    name  = (await name_el.inner_text()).strip()  if name_el  else ""
    size  = (await size_el.inner_text()).strip()  if size_el  else ""
    price = None

    if price_el:
        raw = (await price_el.inner_text()).strip()
        m = re.search(r"\$?([\d]+\.[\d]{2})", raw)
        if m:
            price = float(m.group(1))

    return name, price, size


async def _meijer_store_search(page: Page, product: str, zip_code: str) -> tuple[str, Optional[float], str]:
    """Try Meijer's internal product search API via network interception."""
    result: list[dict] = []

    async def handle_response(response):
        if "product" in response.url and response.status == 200:
            try:
                body = await response.json()
                result.append(body)
            except Exception:
                pass

    page.on("response", handle_response)

    # Trigger the search
    await page.goto(
        f"https://www.meijer.com/search.html#{product.replace(' ', '+')}",
        wait_until="networkidle",
        timeout=30_000,
    )

    page.remove_listener("response", handle_response)

    # Try to extract price from intercepted API responses
    for body in result:
        # Navigate common response shapes
        products_list = (
            body.get("products")
            or body.get("productSearchResult", {}).get("products", [])
            or body.get("data", {}).get("products", [])
        )
        if products_list:
            p      = products_list[0]
            name   = p.get("name") or p.get("displayName", "")
            size   = p.get("size") or p.get("uom", "")
            raw_p  = p.get("price") or p.get("lowPrice") or p.get("saleable", {}).get("price")
            try:
                price = float(str(raw_p).replace("$", "").strip()) if raw_p else None
            except ValueError:
                price = None
            return name, price, size

    # Fall back to DOM parsing
    return await _meijer_search_page(page, product)


async def check_meijer_async(products: list[str]) -> list[PriceResult]:
    results: list[PriceResult] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # Meijer prices are per-store, but Zionsville/Whitestown are so close
        # that we can do one search and attribute to both (same store region).
        # To be precise, we'd change the store; here we note the caveat.
        for loc_name, zip_code in LOCATIONS.items():
            print(f"  Meijer [{loc_name}]:")
            for product in products:
                print(f"    {product}")
                try:
                    name, price, size = await _meijer_store_search(page, product, zip_code)
                    results.append(PriceResult("Meijer", loc_name, product, name, price, size,
                        error="" if name != "Not found" else "Not found"))
                    await asyncio.sleep(1.0)
                except Exception as e:
                    results.append(PriceResult("Meijer", loc_name, product, "", None, error=str(e)))

        await browser.close()

    return results


# ─── ALDI (Playwright) ────────────────────────────────────────────────────────
# ALDI pricing is national — same price regardless of store location.
# We run one search per product and apply it to both locations.

async def _aldi_search(page: Page, product: str) -> tuple[str, Optional[float], str]:
    await page.goto(
        f"https://new.aldi.us/results?q={product.replace(' ', '+')}",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    try:
        await page.wait_for_selector(
            "[class*='product-tile'], [class*='ProductTile'], [data-testid*='product'], "
            "[class*='product-cell'], article",
            timeout=15_000,
        )
    except Exception:
        return "Not found", None, ""

    cards = await page.query_selector_all(
        "[class*='product-tile'], [class*='ProductTile'], "
        "[data-testid*='product-tile'], [class*='product-cell'], article"
    )
    if not cards:
        return "Not found", None, ""

    card = cards[0]

    name_el  = await card.query_selector(
        "[class*='product-title'], [class*='ProductTitle'], [class*='name'], h3, h2, h4, span[class*='title']"
    )
    price_el = await card.query_selector(
        "[class*='price'], [class*='Price'], [data-testid*='price'], span[class*='dollar']"
    )

    name  = (await name_el.inner_text()).strip() if name_el  else ""
    price = None

    if price_el:
        raw = (await price_el.inner_text()).strip()
        m   = re.search(r"\$?([\d]+\.[\d]{2})", raw)
        if m:
            price = float(m.group(1))

    return name, price, ""


async def check_aldi_async(products: list[str]) -> list[PriceResult]:
    results: list[PriceResult] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        print("  ALDI (national pricing — same for all locations):")

        for product in products:
            print(f"    {product}")
            try:
                name, price, size = await _aldi_search(page, product)
                # Apply to both locations
                for loc_name in LOCATIONS:
                    results.append(PriceResult("ALDI", loc_name, product, name, price, size,
                        error="" if name not in ("", "Not found") else "Not found"))
                await asyncio.sleep(1.5)
            except Exception as e:
                for loc_name in LOCATIONS:
                    results.append(PriceResult("ALDI", loc_name, product, "", None, error=str(e)))

        await browser.close()

    return results


# ─── Output ───────────────────────────────────────────────────────────────────

def print_results(results: list[PriceResult]):
    rows = []
    for r in results:
        rows.append([
            r.store,
            r.location,
            r.product_query,
            (r.product_name[:45] + "…") if len(r.product_name) > 45 else r.product_name,
            r.price_str,
            r.size,
        ])

    headers = ["Store", "Location", "Query", "Product Found", "Price", "Size"]
    if HAS_TABULATE:
        print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))
    else:
        widths = [max(len(str(row[i])) for row in [headers] + rows) for i in range(len(headers))]
        fmt    = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print(fmt.format(*[str(c) for c in row]))


def save_csv(results: list[PriceResult], filename: str):
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Store", "Location", "Query", "Product Found", "Price", "Size", "Error"])
        for r in results:
            writer.writerow([
                r.store, r.location, r.product_query, r.product_name,
                r.price if r.price is not None else "", r.size, r.error,
            ])
    print(f"\nSaved to {filename}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_products() -> list[str]:
    if len(sys.argv) < 2:
        print("Usage: python grocery_price_checker.py \"milk, eggs, bread\"")
        print("   or: python grocery_price_checker.py milk eggs bread butter")
        sys.exit(1)

    raw = " ".join(sys.argv[1:])
    if "," in raw:
        products = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        products = [p.strip() for p in sys.argv[1:] if p.strip()]

    return products


async def main():
    products = parse_products()

    print("=" * 60)
    print("Grocery Price Checker")
    print(f"Locations : {', '.join(LOCATIONS.keys())}")
    print(f"Products  : {', '.join(products)}")
    print("=" * 60)
    print()

    all_results: list[PriceResult] = []

    print("Checking Kroger (API)...")
    all_results.extend(check_kroger(products))

    print("\nChecking Meijer (browser)...")
    all_results.extend(await check_meijer_async(products))

    print("\nChecking ALDI (browser)...")
    all_results.extend(await check_aldi_async(products))

    print("\n" + "=" * 60)
    print_results(all_results)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"grocery_prices_{ts}.csv"
    save_csv(all_results, filename)


if __name__ == "__main__":
    asyncio.run(main())
