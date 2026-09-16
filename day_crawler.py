from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from contextlib import contextmanager, nullcontext
import html as html_lib
import json
import re
import shutil
import subprocess
import tempfile
import os
import time
from typing import Any

import pandas as pd
import pymysql
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .amazon_sites import SITE_DOMAIN_MAP, get_site_domain

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions
except Exception:  # pragma: no cover
    webdriver = None
    ChromeOptions = None
    EdgeOptions = None

from .importer import DEFAULT_DATABASE, connect, quote_identifier


SITE_LOCALE_MAP = {
    "U": "en-US,en;q=0.9",
    "M": "es-MX,es;q=0.9,en;q=0.8",
    "G": "de-DE,de;q=0.9,en;q=0.8",
    "E": "en-GB,en;q=0.9",
    "C": "en-CA,en;q=0.9,fr-CA;q=0.8",
    "F": "fr-FR,fr;q=0.9,en;q=0.8",
    "I": "it-IT,it;q=0.9,en;q=0.8",
    "S": "es-ES,es;q=0.9,en;q=0.8",
    "A": "en-AU,en;q=0.9",
}

SITE_CURRENCY_MAP = {
    "U": "$",
    "M": "$",
    "G": "€",
    "E": "£",
    "C": "$",
    "F": "€",
    "I": "€",
    "S": "€",
    "A": "$",
}

SITE_CURRENCY_CODES = {
    "U": {"USD"},
    "M": {"MXN"},
    "G": {"EUR"},
    "E": {"GBP"},
    "C": {"CAD"},
    "F": {"EUR"},
    "I": {"EUR"},
    "S": {"EUR"},
    "A": {"AUD"},
}

SITE_POSTAL_CODE_MAP = {
    "U": "10001",
    "M": "06600",
    "G": "10115",
    "E": "SW1A 1AA",
    "C": "M5V 2T6",
    "F": "75001",
    "I": "00118",
    "S": "28001",
    "A": "2000",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

EDGE_BINARY_CANDIDATES = (
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Users\Administrator\AppData\Local\Microsoft\Edge\Application\msedge.exe",
)

BLOCK_MARKERS = (
    "Robot Check",
    "validateCaptcha",
    "Enter the characters you see below",
    "To discuss automated access to Amazon data",
    "/errors/validateCaptcha",
)


@dataclass
class CrawlMetric:
    buy_box: int | None = None
    price: str | None = None
    star: str | None = None
    reviews: str | None = None
    bsr: str | None = None
    inventory: str | None = None
    follow_sale: str | None = None

    def merge(self, other: "CrawlMetric") -> None:
        for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "follow_sale"):
            if getattr(self, field_name) is None:
                value = getattr(other, field_name)
                if value is not None:
                    setattr(self, field_name, value)

    def is_complete(self) -> bool:
        return all(
            getattr(self, field_name) is not None
            for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory")
        )

    def has_any_value(self) -> bool:
        return any(
            getattr(self, field_name) is not None
            for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "follow_sale")
        )


@dataclass
class CrawlAttempt:
    method: str
    url: str
    success: bool
    message: str = ""


@dataclass
class CrawlRecord:
    data_id: int
    asin: str
    asin_country: str
    country_code: str
    count_day: date
    name: str | None = None
    metrics: CrawlMetric = field(default_factory=CrawlMetric)
    attempts: list[CrawlAttempt] = field(default_factory=list)
    db_action: str = "skipped"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_id": self.data_id,
            "asin": self.asin,
            "asin_country": self.asin_country,
            "country_code": self.country_code,
            "count_day": self.count_day.isoformat(),
            "name": self.name,
            "metrics": {
                "buy_box": self.metrics.buy_box,
                "price": self.metrics.price,
                "star": self.metrics.star,
                "reviews": self.metrics.reviews,
                "bsr": self.metrics.bsr,
                "inventory": self.metrics.inventory,
                "follow_sale": self.metrics.follow_sale,
            },
            "attempts": [attempt.__dict__ for attempt in self.attempts],
            "db_action": self.db_action,
            "error": self.error,
        }


def build_session(locale: str) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    cookie_file = os.getenv("AMAZON_COOKIE_FILE", "").strip()
    if cookie_file:
        session.cookies.update(load_netscape_cookies(cookie_file))
    retry = Retry(
        total=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": locale,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return session


def load_netscape_cookies(cookie_file: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    path = Path(cookie_file)
    if not path.exists():
        return cookies

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#HttpOnly_"):
            line = line.replace("#HttpOnly_", "", 1)
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def normalize_space(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def normalize_digits(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return digits or None


def is_blocked_html(html: str) -> bool:
    lowered = html.lower()
    if any(marker.lower() in lowered for marker in BLOCK_MARKERS):
        return True
    if 'action="/errors/validatecaptcha"' in lowered:
        return True
    if 'id="captchacharacters"' in lowered:
        return True
    return False


def get_locale(country_code: str) -> str:
    return SITE_LOCALE_MAP.get(country_code.upper(), "en-US,en;q=0.9")


def get_currency(country_code: str) -> str:
    return SITE_CURRENCY_MAP.get(country_code.upper(), "")


def price_matches_site(price: str, country_code: str) -> bool:
    lowered = price.lower()
    if any(token in lowered for token in ("cny", "cn¥", "rmb", "人民币")):
        return False
    return True


def get_postal_code(country_code: str) -> str:
    code = country_code.upper()
    return (
        os.getenv(f"AMAZON_POSTAL_{code}", "").strip()
        or SITE_POSTAL_CODE_MAP.get(code)
        or "10001"
    )


def find_edge_binary() -> str | None:
    path_from_env = os.getenv("EDGE_BINARY_PATH", "").strip()
    if path_from_env and Path(path_from_env).exists():
        return path_from_env

    command_path = shutil.which("msedge.exe") or shutil.which("msedge")
    if command_path:
        return command_path

    for candidate in EDGE_BINARY_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def build_candidate_urls(country_code: str, asin: str) -> list[tuple[str, str]]:
    domain = get_site_domain(country_code)
    return [
        ("mobile-aw", f"https://{domain}/gp/aw/d/{asin}"),
        ("product-dp", f"https://{domain}/dp/{asin}"),
        ("product-gp", f"https://{domain}/gp/product/{asin}"),
        ("reviews", f"https://{domain}/product-reviews/{asin}"),
    ]


def fetch_requests_html(session: requests.Session, url: str, locale: str) -> str:
    response = session.get(
        url,
        timeout=20,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": locale,
        },
    )
    response.raise_for_status()
    html = response.text
    if is_blocked_html(html):
        raise RuntimeError("blocked by Amazon captcha or robot check")
    return html


def set_delivery_location_requests(session: requests.Session, country_code: str, locale: str) -> None:
    postal_code = get_postal_code(country_code)
    domain = get_site_domain(country_code)
    endpoint = f"https://{domain}/gp/delivery/ajax/address-change.html"
    params = {
        "locationType": "LOCATION_INPUT",
        "zipCode": postal_code,
        "storeContext": "generic",
        "deviceType": "web",
        "pageType": "Detail",
        "actionSource": "glow",
    }
    try:
        session.post(
            endpoint,
            data=params,
            timeout=12,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": locale,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": f"https://{domain}/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except Exception:
        return


def fetch_curl_html(url: str, locale: str) -> str:
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl executable unavailable")

    command = [
        curl_path,
        "--location",
        "--silent",
        "--show-error",
        "--compressed",
        "--http1.1",
        "--max-time",
        "25",
        "--connect-timeout",
        "10",
        "--noproxy",
        "*",
        "-H",
        f"User-Agent: {USER_AGENT}",
        "-H",
        f"Accept-Language: {locale}",
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "-H",
        "Cache-Control: no-cache",
        "-H",
        "Pragma: no-cache",
        "-H",
        "Upgrade-Insecure-Requests: 1",
    ]
    cookie_file = os.getenv("AMAZON_COOKIE_FILE", "").strip()
    cookie_path = Path(cookie_file) if cookie_file else get_curl_cookie_jar(country_from_url(url))
    command.extend(["--cookie", str(cookie_path), "--cookie-jar", str(cookie_path), url])

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if completed.returncode != 0:
        message = normalize_space(completed.stderr) or f"curl exited with {completed.returncode}"
        raise RuntimeError(message)

    html = completed.stdout
    if not html:
        raise RuntimeError("curl returned empty response")
    if is_blocked_html(html):
        raise RuntimeError("blocked by Amazon captcha or robot check")
    return html


def set_delivery_location_curl(country_code: str, locale: str) -> None:
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        return

    postal_code = get_postal_code(country_code)
    domain = get_site_domain(country_code)
    cookie_jar = get_curl_cookie_jar(country_code)
    endpoint = f"https://{domain}/gp/delivery/ajax/address-change.html"
    command = [
        curl_path,
        "--location",
        "--silent",
        "--show-error",
        "--compressed",
        "--http1.1",
        "--max-time",
        "15",
        "--connect-timeout",
        "8",
        "--noproxy",
        "*",
        "--cookie-jar",
        str(cookie_jar),
        "--cookie",
        str(cookie_jar),
        "--request",
        "POST",
        "-H",
        f"User-Agent: {USER_AGENT}",
        "-H",
        f"Accept-Language: {locale}",
        "-H",
        "Accept: application/json, text/javascript, */*; q=0.01",
        "-H",
        "X-Requested-With: XMLHttpRequest",
        "-H",
        f"Referer: https://{domain}/",
        "--data-urlencode",
        "locationType=LOCATION_INPUT",
        "--data-urlencode",
        f"zipCode={postal_code}",
        "--data-urlencode",
        "storeContext=generic",
        "--data-urlencode",
        "deviceType=web",
        "--data-urlencode",
        "pageType=Detail",
        "--data-urlencode",
        "actionSource=glow",
        endpoint,
    ]
    try:
        subprocess.run(command, check=False, capture_output=True, timeout=20)
    except Exception:
        return


def get_curl_cookie_jar(country_code: str) -> Path:
    cookie_file = os.getenv("AMAZON_COOKIE_FILE", "").strip()
    if cookie_file:
        return Path(cookie_file)
    cookie_dir = Path(__file__).resolve().parent.parent / ".amazon_curl_cookies"
    cookie_dir.mkdir(exist_ok=True)
    return cookie_dir / f"{country_code.upper()}.txt"


def create_selenium_driver(locale: str):
    if webdriver is None:
        raise RuntimeError("selenium webdriver unavailable")

    temp_root = Path(__file__).resolve().parent.parent / ".selenium_tmp"
    temp_root.mkdir(exist_ok=True)
    persistent_profile = os.getenv("AMAZON_SELENIUM_PROFILE_DIR", "").strip()
    if persistent_profile:
        profile_context = None
        profile_dir = str(Path(persistent_profile).resolve())
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
    else:
        profile_context = tempfile.TemporaryDirectory(prefix="amazon_selenium_", dir=str(temp_root))
        profile_dir = profile_context.name

    try:
        browser = os.getenv("AMAZON_SELENIUM_BROWSER", "edge").strip().lower()
        if browser == "edge" and EdgeOptions is not None:
            options = EdgeOptions()
            options.page_load_strategy = "eager"
            edge_binary = find_edge_binary()
            if edge_binary:
                options.binary_location = edge_binary
            driver_factory = webdriver.Edge
        elif ChromeOptions is not None:
            options = ChromeOptions()
            options.page_load_strategy = "eager"
            driver_factory = webdriver.Chrome
        else:
            raise RuntimeError("selenium browser options unavailable")

        headless = os.getenv("AMAZON_SELENIUM_HEADLESS", "1").lower() not in {"0", "false", "no"}
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-features=Translate,AutomationControlled")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--window-size=1600,2400")
        options.add_argument(f"--user-agent={USER_AGENT}")
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument(f"--lang={locale.split(',')[0]}")

        driver = driver_factory(options=options)
        page_timeout = float(os.getenv("AMAZON_SELENIUM_PAGE_TIMEOUT", "15"))
        driver.set_page_load_timeout(page_timeout)
        return driver, profile_context
    except Exception:
        if profile_context is not None:
            profile_context.cleanup()
        raise


def close_selenium_driver(driver, profile_context) -> None:
    try:
        if driver is not None:
            driver.quit()
    finally:
        if profile_context is not None:
            profile_context.cleanup()


def fetch_selenium_html(url: str, locale: str, driver=None) -> str:
    owns_driver = driver is None
    profile_context = None
    if owns_driver:
        driver, profile_context = create_selenium_driver(locale)

    try:
        if owns_driver:
            country_code = country_from_url(url)
            set_delivery_location_selenium(driver, country_code, locale)
        driver.get(url)
        wait_seconds = float(os.getenv("AMAZON_SELENIUM_WAIT_SECONDS", "3"))
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        html = driver.page_source
        if is_blocked_html(html):
            raise RuntimeError("blocked by Amazon captcha or robot check")
        return html
    finally:
        if owns_driver:
            close_selenium_driver(driver, profile_context)


@contextmanager
def shared_selenium_driver():
    driver = None
    profile_context = None
    try:
        driver, profile_context = create_selenium_driver("en-US,en;q=0.9")
        yield driver
    finally:
        close_selenium_driver(driver, profile_context)


def country_from_url(url: str) -> str:
    lowered = url.lower()
    for country_code, domain in SITE_DOMAIN_MAP.items():
        if domain in lowered:
            return country_code
    return "U"


def set_delivery_location_selenium(driver, country_code: str, locale: str) -> None:
    postal_code = get_postal_code(country_code)
    domain = get_site_domain(country_code)
    endpoint = f"https://{domain}/gp/delivery/ajax/address-change.html"
    try:
        driver.get(f"https://{domain}/")
        script = """
            const [endpoint, body] = arguments;
            const done = arguments[arguments.length - 1];
            fetch(endpoint, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest"
                },
                body
            }).then(() => done(true)).catch(() => done(false));
        """
        body = (
            "locationType=LOCATION_INPUT"
            f"&zipCode={postal_code.replace(' ', '+')}"
            "&storeContext=generic"
            "&deviceType=web"
            "&pageType=Detail"
            "&actionSource=glow"
        )
        driver.set_script_timeout(15)
        driver.execute_async_script(script, endpoint, body)
        time.sleep(1)
    except Exception:
        return


def parse_star(soup: BeautifulSoup, text: str) -> str | None:
    selectors = [
        'i[data-hook="average-star-rating"] span',
        'span[data-hook="rating-out-of-text"]',
        'span.a-icon-alt',
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+out of 5 stars", value, re.I)
                if match:
                    return match.group(1)
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
                if match:
                    return match.group(1)
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+out of 5 stars", text, re.I)
    if match:
        return match.group(1)
    return None


def parse_buy_box(soup: BeautifulSoup, text: str) -> int | None:
    selectors = [
        "#buybox",
        "#buybox-see-all-buying-choices",
        "#add-to-cart-button",
        "#buy-now-button",
        "#merchant-info",
    ]
    text_parts: list[str] = []
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                text_parts.append(value)

    candidate = " ".join(text_parts + [text]).strip()
    lowered = candidate.lower()

    if any(marker in lowered for marker in ("currently unavailable", "temporarily out of stock")):
        return 0
    if "see all buying options" in lowered or "see all options" in lowered:
        return 0
    if any(marker in lowered for marker in ("add to cart", "buy now", "add to basket")):
        return 1

    merchant = soup.select_one("#merchant-info")
    if merchant:
        merchant_text = normalize_space(merchant.get_text(" ", strip=True)) or ""
        merchant_lowered = merchant_text.lower()
        if any(
            marker in merchant_lowered
            for marker in (
                "ships from and sold by amazon",
                "fulfilled by amazon",
                "sold by amazon",
            )
        ):
            return 1

    if soup.select_one("#buybox") or soup.select_one("#add-to-cart-button"):
        return 1

    return None


def parse_price(
    soup: BeautifulSoup,
    raw_html: str | None = None,
    currency: str = "",
    country_code: str = "",
) -> str | None:
    selectors = [
        "#corePrice_desktop .apex-core-price-identifier .a-offscreen",
        "#corePrice_desktop .a-price .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#apex_desktop .a-offscreen",
        "#buybox .a-price .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.a-price span.a-offscreen",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value and re.search(r"\d", value) and price_matches_site(value, country_code):
                return value

    if raw_html:
        decoded_html = html_lib.unescape(raw_html).replace('\\"', '"')
        buybox_patterns = [
            r'"desktop_buybox_group_1"\s*:\s*\[\s*\{.*?"displayPrice"\s*:\s*"([^"]*[0-9][^"]*)"',
            r'"priceToPay"\s*:\s*\{.*?"amount"\s*:\s*([0-9]+(?:[.,][0-9]+)?)',
        ]
        for pattern in buybox_patterns:
            match = re.search(pattern, decoded_html, re.I | re.S)
            if not match:
                continue
            value = normalize_space(match.group(1))
            if value and price_matches_site(value, country_code):
                return value

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            offers_list = offers if isinstance(offers, list) else [offers]
            for offer in offers_list:
                if not isinstance(offer, dict) or offer.get("price") in (None, ""):
                    continue
                price = str(offer["price"]).strip()
                offer_currency = str(offer.get("priceCurrency") or "").strip()
                value = f"{offer_currency} {price}" if offer_currency else price
                if price_matches_site(value, country_code):
                    return value

    if raw_html:
        decoded_html = html_lib.unescape(raw_html).replace('\\"', '"')
        patterns = [
            r'"price"\s*:\s*"([^"]*[0-9][^"]*)"',
            r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'"displayPrice"\s*:\s*"([^"]*[0-9][^"]*)"',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, decoded_html, re.I):
                value = normalize_space(match.group(1))
                if not value or "http" in value.lower() or len(value) > 40:
                    continue
                if not price_matches_site(value, country_code):
                    continue
                if re.fullmatch(r"[0-9]+(?:[\.,][0-9]+)?", value) and currency:
                    return f"{currency}{value}"
                return value

    return None


def parse_reviews(soup: BeautifulSoup, text: str) -> str | None:
    selectors = [
        '#acrCustomerReviewText',
        'span[data-hook="total-review-count"]',
        'span[data-hook="cr-filter-info-review-rating-count"]',
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                match = re.search(r"([\d,]+)", value)
                if match:
                    return match.group(1).replace(",", "")

    patterns = [
        r"([\d,]+)\s+customer reviews?",
        r"([\d,]+)\s+ratings?",
        r"([\d,]+)\s+global ratings?",
        r"([\d,]+)\s+review(?:s)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).replace(",", "")
    return None


def parse_bsr(text: str, raw_html: str | None = None) -> str | None:
    if raw_html:
        decoded_html = html_lib.unescape(raw_html).replace('\\"', '"')
        for marker in ("salesRanks", "salesRank"):
            marker_index = decoded_html.lower().find(marker.lower())
            if marker_index < 0:
                continue
            snippet = decoded_html[marker_index : marker_index + 12000]
            rankings = re.findall(
                r'"rank"\s*:\s*(\d+).*?"rankedCategoryType"\s*:\s*"([^"]+)"',
                snippet,
                re.I | re.S,
            )
            if rankings:
                store_rank = next(
                    (rank for rank, category_type in rankings if category_type.upper() == "STORE"),
                    None,
                )
                return store_rank or rankings[0][0]

    normalized = normalize_space(text) or ""
    lowered = normalized.lower()
    rank_labels = (
        "best sellers rank",
        "bestseller-rang",
        "clasificación de los más vendidos",
        "clasificacion de los mas vendidos",
        "rang des meilleures ventes",
        "classifica nelle vendite",
        "classifica dei bestseller",
        "classificação dos mais vendidos",
        "ranking de ventas",
    )
    snippets: list[str] = []
    for marker in rank_labels:
        idx = lowered.find(marker)
        if idx >= 0:
            snippets.append(normalized[idx : idx + 1200])
    snippets.append(normalized)
    patterns = [
        r"#\s*([\d,.]+)\s+(?:in|en|im|dans|di|de|em|a)\b",
        r"(?:rank|rang|clasificaci[oó]n|classifica|ranking)[^#\d]{0,120}#?\s*([\d,.]+)",
        r"(?:salesrank|sales_rank|best_sellers_rank)[^0-9]{0,80}([0-9][\d,.]*)",
    ]
    for snippet in snippets:
        for pattern in patterns:
            match = re.search(pattern, snippet, re.I | re.S)
            if match:
                value = match.group(1).replace(",", "").replace(".", "").strip()
                if value:
                    return value
    return None


def parse_inventory(soup: BeautifulSoup, text: str) -> str | None:
    selectors = [
        "#availability",
        "#outOfStock",
        "#availability span",
    ]
    candidates: list[str] = []
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                candidates.append(value)

    if not candidates:
        candidates.append(text)

    for candidate in candidates:
        lowered = candidate.lower()
        match = re.search(r"only\s+(\d+)\s+left", lowered)
        if match:
            return match.group(1)
        match = re.search(r"(\d+)\s+left in stock", lowered)
        if match:
            return match.group(1)
        match = re.search(r"(?:solo quedan|s[oó]lo quedan|nur noch)\s+(\d+)", lowered)
        if match:
            return match.group(1)
        if any(
            word in lowered
            for word in (
                "currently unavailable",
                "out of stock",
                "temporarily out of stock",
                "no disponible",
                "nicht verfugbar",
                "non disponibile",
                "indisponible",
            )
        ):
            return "0"
        if any(
            word in lowered
            for word in (
                "in stock",
                "available",
                "disponible",
                "auf lager",
                "en stock",
                "disponibile",
                "disponível",
            )
        ):
            return "1"
    return None


def parse_follow_sale(soup: BeautifulSoup, text: str, raw_html: str | None = None) -> str | None:
    selectors = [
        "#olpLinkWidget_feature_div",
        "#olpLinkWidget",
        "#buybox-see-all-buying-choices",
        "#aod-container",
        "#aod-offer-list",
        "#merchant-info",
    ]
    candidates: list[str] = []
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                candidates.append(value)

    if raw_html:
        decoded_html = html_lib.unescape(raw_html).replace('\\"', '"')
        candidates.append(decoded_html[:200000])
    candidates.append(text)

    patterns = [
        r"(\d+)\s+(?:new\s+)?(?:offers?|sellers?)",
        r"available from\s+(\d+)\s+sellers?",
        r"(\d+)\s+other\s+sellers?",
        r"see all\s+(\d+)\s+(?:buying\s+)?options?",
        r"(\d+)\s+buying\s+options?",
        r"(?:new\s*&\s*used|used\s*&\s*new|new\s+and\s+used)\s*\(\s*(\d+)\s*\)",
        r"(\d+)\s+(?:results?|offers?|options?)\b",
        r"(?:from|of)\s+(\d+)\s+(?:sellers?|merchants?|offers?|options?)",
        r"(?:vendedor(?:es)?|ofertas?|opciones?)\s*[:：]?\s*(\d+)",
    ]
    for candidate in candidates:
        lowered = candidate.lower()
        for pattern in patterns:
            match = re.search(pattern, lowered, re.I)
            if match:
                return match.group(1)

    if raw_html:
        decoded_html = html_lib.unescape(raw_html).replace('\\"', '"')
        structured_patterns = [
            r'"(?:offerCount|totalOffers|numberOfOffers|offerListingCount|sellerCount|merchantCount)"\s*:\s*"?(\d+)',
            r'"(?:buyingOptionsCount|buyingOptionCount)"\s*:\s*"?(\d+)',
        ]
        for pattern in structured_patterns:
            match = re.search(pattern, decoded_html, re.I)
            if match:
                return match.group(1)

    offer_nodes = soup.select("#aod-offer, .aod-offer")
    if offer_nodes:
        return str(len(offer_nodes))

    return None


def extract_metrics(html: str, country_code: str = "") -> CrawlMetric:
    soup = BeautifulSoup(html, "html.parser")
    text = normalize_space(soup.get_text(" ", strip=True)) or ""
    return CrawlMetric(
        buy_box=parse_buy_box(soup, text),
        price=parse_price(soup, html, get_currency(country_code), country_code),
        star=parse_star(soup, text),
        reviews=parse_reviews(soup, text),
        bsr=parse_bsr(text, html),
        inventory=parse_inventory(soup, text),
        follow_sale=parse_follow_sale(soup, text, html),
    )


def crawl_item_metrics(
    country_code: str,
    asin: str,
    selenium_driver=None,
    selenium_location_state: dict[str, str] | None = None,
) -> tuple[CrawlMetric, list[CrawlAttempt]]:
    locale = get_locale(country_code)
    session = build_session(locale)
    set_delivery_location_requests(session, country_code, locale)
    set_delivery_location_curl(country_code, locale)
    attempts: list[CrawlAttempt] = []
    result = CrawlMetric()

    candidate_urls = build_candidate_urls(country_code, asin)
    # Browser rendering is expensive and often unavailable on servers. Keep it
    # as an explicit fallback after all lightweight requests have been tried.
    selenium_only = (
        selenium_driver is not None
        and os.getenv("AMAZON_SELENIUM_ONLY", "").lower() in {"1", "true", "yes"}
    )
    if not selenium_only:
        for url_name, url in candidate_urls:
            if result.is_complete():
                break
            try:
                html = fetch_requests_html(session, url, locale)
                parsed = extract_metrics(html, country_code)
                result.merge(parsed)
                attempts.append(
                    CrawlAttempt(
                        method=f"requests:{url_name}",
                        url=url,
                        success=True,
                        message=f"filled={_filled_fields(parsed)}",
                    )
                )
            except Exception as exc:
                attempts.append(
                    CrawlAttempt(
                        method=f"requests:{url_name}",
                        url=url,
                        success=False,
                        message=str(exc),
                    )
                )

    if not result.is_complete() and not selenium_only:
        for url_name, url in candidate_urls:
            if result.is_complete():
                break
            try:
                html = fetch_curl_html(url, locale)
                parsed = extract_metrics(html, country_code)
                result.merge(parsed)
                attempts.append(
                    CrawlAttempt(
                        method=f"curl:{url_name}",
                        url=url,
                        success=True,
                        message=f"filled={_filled_fields(parsed)}",
                    )
                )
            except Exception as exc:
                attempts.append(
                    CrawlAttempt(
                        method=f"curl:{url_name}",
                        url=url,
                        success=False,
                        message=str(exc),
                    )
                )

    enable_selenium = os.getenv("AMAZON_ENABLE_SELENIUM", "").lower() in {"1", "true", "yes"}
    if not result.is_complete() and enable_selenium:
        if selenium_driver is not None and (
            selenium_location_state is None
            or selenium_location_state.get("country") != country_code
        ):
            set_delivery_location_selenium(selenium_driver, country_code, locale)
            if selenium_location_state is not None:
                selenium_location_state["country"] = country_code
        selenium_urls = candidate_urls
        if os.getenv("AMAZON_SELENIUM_FAST", "").lower() in {"1", "true", "yes"}:
            selenium_urls = candidate_urls[:1]
        for url_name, url in selenium_urls:
            try:
                html = fetch_selenium_html(url, locale, driver=selenium_driver)
                parsed = extract_metrics(html, country_code)
                result.merge(parsed)
                attempts.append(
                    CrawlAttempt(
                        method=f"selenium:{url_name}",
                        url=url,
                        success=True,
                        message=f"filled={_filled_fields(parsed)}",
                    )
                )
                if result.is_complete():
                    break
            except Exception as exc:
                attempts.append(
                    CrawlAttempt(
                        method=f"selenium:{url_name}",
                        url=url,
                        success=False,
                        message=str(exc),
                    )
                )

    return result, attempts


def _filled_fields(metric: CrawlMetric) -> list[str]:
    fields = []
    for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "follow_sale"):
        if getattr(metric, field_name) is not None:
            fields.append(field_name)
    return fields


def fetch_targets(
    connection,
    data_id: int | None = None,
    data_ids: list[int] | None = None,
    asin_country: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = [
        "SELECT id, asin, country, asin_country, name",
        f"FROM {quote_identifier('amazon_data')}",
    ]
    where_parts: list[str] = []
    params: list[Any] = []

    if data_id is not None:
        where_parts.append("id = %s")
        params.append(int(data_id))
    if data_ids:
        placeholders = ", ".join(["%s"] * len(data_ids))
        where_parts.append(f"id IN ({placeholders})")
        params.extend(int(item) for item in data_ids)
    if asin_country:
        where_parts.append("asin_country = %s")
        params.append(asin_country)
    if data_id is None and not data_ids and not asin_country:
        where_parts.append("status = 1")

    if where_parts:
        sql.append("WHERE " + " AND ".join(where_parts))

    sql.append("ORDER BY id ASC")
    if limit is not None:
        sql.append("LIMIT %s")
        params.append(int(limit))

    full_sql = " ".join(sql)
    with connection.cursor() as cursor:
        cursor.execute(full_sql, params)
        return list(cursor.fetchall())


def fetch_existing_day_id(connection, data_id: int, count_day: date) -> int | None:
    sql = (
        f"SELECT id FROM {quote_identifier('asin_day_data')} "
        "WHERE data_id = %s AND count_day = %s LIMIT 1"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, (data_id, count_day))
        row = cursor.fetchone()
        if row:
            return int(row["id"])
    return None


def upsert_day_record(
    connection,
    data_id: int,
    asin_country: str,
    name: str | None,
    metrics: CrawlMetric,
    count_day: date,
    fill_missing_only: bool = False,
) -> str:
    existing_id = fetch_existing_day_id(connection, data_id, count_day)
    values = {
        "data_id": data_id,
        "asin_country": asin_country,
        "name": name[:20] if name else None,
        "buy_box": metrics.buy_box,
        "price": metrics.price,
        "star": metrics.star,
        "reviews": metrics.reviews,
        "bsr": metrics.bsr,
        "inventory": metrics.inventory,
        "follow_sale": metrics.follow_sale,
        "count_day": count_day,
    }

    if existing_id is not None:
        existing_values = None
        if fill_missing_only:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {quote_identifier('asin_day_data')} WHERE id = %s LIMIT 1",
                    (existing_id,),
                )
                existing_values = cursor.fetchone() or {}
        updates = {
            column: value
            for column, value in values.items()
            if column not in {"data_id", "count_day"} and value is not None
            and (not fill_missing_only or existing_values.get(column) in (None, ""))
        }
        if not updates:
            return "unchanged"

        set_sql = ", ".join(f"{quote_identifier(column)} = %s" for column in updates)
        sql = f"UPDATE {quote_identifier('asin_day_data')} SET {set_sql} WHERE id = %s"
        params = list(updates.values()) + [existing_id]
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
        connection.commit()
        return "updated"

    insert_values = {column: value for column, value in values.items() if value is not None}
    if not any(
        insert_values.get(column) is not None
        for column in ("buy_box", "price", "star", "reviews", "bsr", "inventory")
    ):
        return "skipped"

    columns = ", ".join(quote_identifier(column) for column in insert_values)
    placeholders = ", ".join(["%s"] * len(insert_values))
    sql = f"INSERT INTO {quote_identifier('asin_day_data')} ({columns}) VALUES ({placeholders})"
    with connection.cursor() as cursor:
        cursor.execute(sql, list(insert_values.values()))
    connection.commit()
    return "inserted"


def crawl_amazon_day_data(
    connection: pymysql.connections.Connection,
    data_id: int | None = None,
    data_ids: list[int] | None = None,
    asin_country: str | None = None,
    limit: int | None = None,
    fill_missing_only: bool = False,
) -> dict[str, Any]:
    targets = fetch_targets(connection, data_id=data_id, data_ids=data_ids, asin_country=asin_country, limit=limit)
    today = date.today()
    items: list[dict[str, Any]] = []
    stats = {
        "total_targets": len(targets),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }

    selenium_enabled = os.getenv("AMAZON_ENABLE_SELENIUM", "").lower() in {"1", "true", "yes"}
    driver_context = shared_selenium_driver() if selenium_enabled else nullcontext(None)
    selenium_location_state: dict[str, str] = {}
    try:
        with driver_context as selenium_driver:
            for target in targets:
                record = CrawlRecord(
                    data_id=int(target["id"]),
                    asin=str(target["asin"]),
                    asin_country=str(target["asin_country"] or ""),
                    country_code=str(target["country"] or "U"),
                    count_day=today,
                    name=str(target["name"] or "") or None,
                )

                try:
                    metrics, attempts = crawl_item_metrics(
                        record.country_code,
                        record.asin,
                        selenium_driver=selenium_driver,
                        selenium_location_state=selenium_location_state,
                    )
                    record.metrics = metrics
                    record.attempts = attempts
                    if metrics.has_any_value():
                        action = upsert_day_record(
                            connection,
                            record.data_id,
                            record.asin_country,
                            record.name,
                            metrics,
                            today,
                            fill_missing_only=fill_missing_only,
                        )
                        record.db_action = action
                        if action in stats:
                            stats[action] += 1
                    else:
                        record.db_action = "skipped"
                        record.error = "no metrics captured"
                        stats["skipped"] += 1
                except Exception as exc:
                    record.db_action = "failed"
                    record.error = str(exc)
                    stats["failed"] += 1

                items.append(record.to_dict())
    except Exception as exc:
        if not items:
            raise
        stats["failed"] += len(targets) - len(items)
        for target in targets[len(items) :]:
            items.append(
                {
                    "data_id": int(target["id"]),
                    "asin": str(target["asin"]),
                    "asin_country": str(target["asin_country"] or ""),
                    "country_code": str(target["country"] or "U"),
                    "count_day": today.isoformat(),
                    "metrics": CrawlMetric().__dict__,
                    "attempts": [],
                    "db_action": "failed",
                    "error": str(exc),
                }
            )

    return {"stats": stats, "items": items}
