from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import html as html_lib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import os
import time
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import pymysql
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .amazon_sites import SITE_DOMAIN_MAP, get_site_domain

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
except Exception:  # pragma: no cover
    webdriver = None
    ChromeOptions = None
    ChromeService = None
    EdgeOptions = None
    TimeoutException = TimeoutError

from .importer import DEFAULT_DATABASE, connect, quote_identifier


DETAIL_FOUND = "detail_found"
DETAIL_EMPTY = "detail_empty"
DETAIL_UNKNOWN = "fetch_unknown"

CUSTOMER_REVIEW_LABEL_PATTERN = (
    r"customer\s+reviews?|durchschnittliche\s+kundenbewertung|kundenbewertung|"
    r"kundenrezensionen|opini[oó]n\s+media\s+de\s+los\s+clientes|"
    r"valoraci[oó]n\s+media\s+de\s+los\s+clientes|"
    r"commentaires?\s+client|[eé]valuations?\s+client|recensioni\s+(?:dei\s+)?clienti|"
    r"カスタマーレビュー|おすすめ度|推荐度|客户评论|买家评论"
)
BSR_LABEL_PATTERN = (
    r"best\s+sellers?\s+rank|bestseller[- ]rang|"
    r"clasificaci[oó]n\s+en\s+los\s+m[aá]s\s+vendidos|"
    r"ranking\s+de\s+los\s+m[aá]s\s+vendidos|"
    r"classement\s+des\s+meilleures\s+ventes|rang\s+des\s+meilleures\s+ventes|"
    r"posizione\s+nella\s+classifica\s+bestseller|classifica\s+(?:nelle\s+vendite|dei\s+bestseller)|"
    r"(?:Amazon\s*)?売れ筋ランキング|ベストセラーランキング|亚马逊热销商品排行(?:榜|名)"
)
DETAIL_HEADING_PATTERN = (
    r"^(?:item\s+details|artikelangaben|produktdaten|product\s+information|"
    r"informaci[oó]n\s+del\s+producto|detalles\s+del\s+producto|"
    r"technical\s+details|additional\s+information|"
    r"informations?\s+sur\s+le\s+produit|d[eé]tails\s+(?:du\s+produit|techniques)|"
    r"informations?\s+compl[eé]mentaires|informazioni\s+sul\s+prodotto|"
    r"dettagli\s+(?:del\s+prodotto|tecnici)|ulteriori\s+informazioni|登録情報|商品情報|商品の情報|商品信息|商品详情|产品信息)$"
)
STAR_VALUE_PATTERN = (
    r"([0-5](?:[.,][0-9]+)?)\s*(?:out\s+of\s+5\s+stars|von\s+5\s+Sternen|"
    r"de\s+5\s+estrellas|sobre\s+5\s+estrellas|sur\s+5\s+[eé]toiles|su\s+5\s+stelle|"
    r"颗星[，,]?\s*最多\s*5\s*颗星)"
)
JAPANESE_STAR_VALUE_PATTERN = r"5\s*つ星のうち\s*([0-5](?:[.,][0-9]+)?)"


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
    "J": "ja-JP,ja;q=0.9,en;q=0.8",
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
    "J": "¥",
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
    "J": {"JPY"},
}

SITE_POSTAL_CODE_MAP = {
    "U": "10001",
    "M": "11000",
    "G": "10115",
    "E": "SW1A 1AA",
    "C": "T4B 2T3",
    "F": "75001",
    "I": "00118",
    "S": "28001",
    "A": "2000",
    "J": "100-0001",
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

LOCAL_CHROMEDRIVER_PATH = (
    Path(__file__).resolve().parent.parent / ".browser_drivers" / "chrome" / "chromedriver.exe"
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
    deal: list[dict[str, str]] | None = None
    detail_status: str = DETAIL_UNKNOWN
    offer_verified: bool = False

    def merge(self, other: "CrawlMetric") -> None:
        # A confirmed cart offer takes precedence over a sparse page that only
        # established a no-cart state. Price and cart must move together so a
        # price from another offer can never be merged into a no-cart result.
        if other.offer_verified:
            self.offer_verified = True
            if other.buy_box == 1:
                self.buy_box = 1
                if other.price is not None:
                    self.price = other.price
            elif self.buy_box is None and other.buy_box is not None:
                self.buy_box = other.buy_box

        for field_name in ("star", "reviews", "bsr", "inventory", "follow_sale"):
            if getattr(self, field_name) in (None, "", "无"):
                value = getattr(other, field_name)
                if value is not None:
                    setattr(self, field_name, value)
        if other.deal is not None and (self.deal is None or other.deal):
            self.deal = [*other.deal]
        self.merge_detail_status(other)

    def is_complete(self) -> bool:
        # "无" is a confirmed absence from a rendered detail section, not a
        # transient missing value. Unknown offers must still remain pending.
        missing = {"", "none", "null", "n/a", "na"}
        base_complete = str(self.buy_box) in {"0", "1"} and all(
            getattr(self, field_name) is not None
            and str(getattr(self, field_name)).strip().casefold() not in missing
            for field_name in ("star", "reviews", "bsr")
        )
        price_complete = str(self.buy_box) == "0" or self.price not in (None, "", "无")
        return base_complete and price_complete

    def has_any_value(self) -> bool:
        return any(
            getattr(self, field_name) is not None
            for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "follow_sale")
        )

    def merge_detail_status(self, other: "CrawlMetric") -> None:
        if other.detail_status == DETAIL_FOUND:
            self.detail_status = DETAIL_FOUND
        elif self.detail_status == DETAIL_UNKNOWN and other.detail_status == DETAIL_EMPTY:
            self.detail_status = DETAIL_EMPTY


@dataclass
class CrawlAttempt:
    method: str
    url: str
    success: bool
    message: str = ""


class SeleniumSessionError(RuntimeError):
    """The browser session disappeared and can be recreated safely."""

    def __init__(self, message: str, metrics: CrawlMetric, attempts: list[CrawlAttempt]):
        super().__init__(message)
        self.metrics = metrics
        self.attempts = attempts


class ProductLinkUnavailable(RuntimeError):
    """Amazon confirmed that the requested product link no longer exists."""


LINK_UNAVAILABLE_MESSAGE = 'product link unavailable: amazon page not found'


def is_product_link_unavailable_html(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, 'html.parser')
    title = normalize_space(soup.title.get_text(' ', strip=True) if soup.title else '') or ''
    text = normalize_space(soup.get_text(' ', strip=True)) or ''
    content = f'{title} {text}'.casefold()
    return any(marker in content for marker in (
        "we couldn't find that page",
        'we could not find that page',
        'sorry we couldn’t find that page',
        'page not found',
        'the web address you entered is not a functioning page on our site',
        'web address you entered is not a functioning page',
        'looking for something? we’re sorry',
        'looking for something? we\'re sorry',
    )) and not has_product_page(soup)


def is_selenium_session_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "invalid session id",
            "session deleted",
            "not connected to devtools",
            "disconnected: not connected",
            "chrome not reachable",
            "edge not reachable",
            "no such window",
        )
    )


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_hard_block_message(message: str) -> bool:
    lowered = message.casefold()
    return any(marker.casefold() in lowered for marker in (
        "captcha",
        "robot check",
        "blocked by amazon",
        "automated access",
    ))


def is_product_link_unavailable_message(message: str) -> bool:
    lowered = message.casefold()
    return 'product link unavailable' in lowered and 'page not found' in lowered


def classify_failure(message: str) -> str:
    message = message.casefold()
    if is_hard_block_message(message):
        return "blocked"
    if "postal" in message or "delivery" in message:
        return "delivery"
    if "currency" in message or "site mismatch" in message or "asin mismatch" in message:
        return "identity_or_currency"
    if is_selenium_session_error(RuntimeError(message)):
        return "browser_session"
    if "no product details" in message:
        return "no_details"
    return "network_or_incomplete"


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
                "deal": self.metrics.deal,
                "detail_status": self.metrics.detail_status,
                "offer_verified": self.metrics.offer_verified,
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
        total=0 if env_flag("AMAZON_STOP_ON_BLOCK") else 2,
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
    if is_product_link_unavailable_html(html):
        return False
    lowered = html.lower()
    if any(marker.lower() in lowered for marker in BLOCK_MARKERS):
        return True
    if 'action="/errors/validatecaptcha"' in lowered:
        return True
    if 'id="captchacharacters"' in lowered:
        return True
    # Newer interstitials use a Continue shopping / human-confirmation button.
    # Do not confuse ordinary product copy or cart navigation with a challenge.
    if 'id="producttitle"' not in lowered and "id='producttitle'" not in lowered:
        visible = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).casefold()
        if any(marker in visible for marker in (
            'click the button below to continue shopping',
            "verify you're a human", 'verify that you are human',
            '请确认您是真人', '请验证您是真人',
        )):
            return True
    return False


def get_locale(country_code: str) -> str:
    return SITE_LOCALE_MAP.get(country_code.upper(), "en-US,en;q=0.9")


def get_currency(country_code: str) -> str:
    return SITE_CURRENCY_MAP.get(country_code.upper(), "")


def price_matches_site(price: str, country_code: str) -> bool:
    code = country_code.upper()
    lowered = price.lower().replace("\u00a0", " ")
    expected_codes = SITE_CURRENCY_CODES.get(code)
    if not expected_codes:
        return False
    expected_code = next(iter(expected_codes))

    # Explicit currency codes are unambiguous and must belong to the site.
    found_codes = set(re.findall(
        r"(?<![A-Z])(?:USD|MXN|EUR|GBP|CAD|AUD|CNY|RMB|JPY|INR|BRL|KRW)(?![A-Z])",
        lowered.upper(),
    ))
    if found_codes - expected_codes:
        return False

    # Handle country-qualified dollar symbols (US$, MX$, CA$, AU$), as well
    # as the generic '$' used by several Amazon locales.
    dollar_markers = re.findall(r"(?:us|mx|ca|au)\$|\$", lowered)
    if dollar_markers:
        expected_dollar_marker = {
            "U": "us$",
            "M": "mx$",
            "C": "ca$",
            "A": "au$",
        }.get(code)
        if expected_dollar_marker and any(
            marker != "$" and marker != expected_dollar_marker
            for marker in dollar_markers
        ):
            return False
        if code not in {"U", "M", "C", "A"}:
            return False

    if "\u20ac" in price and expected_code != "EUR":
        return False
    if "\u00a3" in price and expected_code != "GBP":
        return False
    if any(token in lowered for token in ("cny", "rmb", "cn¥", "人民币", "元")):
        return False
    if any(symbol in price for symbol in ("₹", "₽", "₩")):
        return False
    if any(symbol in price for symbol in ("¥", "￥", "円")) and code != "J":
        return False
    return True


def format_site_price(value: str, currency: str) -> str:
    value = normalize_space(value) or ""
    if re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", value) and currency:
        return f"{currency}{value}"
    return value


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


def find_chrome_binary() -> str | None:
    configured = os.getenv("CHROME_BINARY_PATH", "").strip()
    if configured:
        if not Path(configured).is_file():
            raise RuntimeError(f"CHROME_BINARY_PATH does not exist: {configured}")
        return str(Path(configured).resolve())
    for name in ("chrome.exe", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    for root in (os.getenv("LOCALAPPDATA"), os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)")):
        if root:
            candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def find_chromedriver_binary() -> str:
    """Use an installed driver; never block a crawl on an implicit download."""
    configured = os.getenv("CHROMEDRIVER_PATH", "").strip()
    if configured:
        candidate = Path(configured).resolve()
        if not candidate.is_file():
            raise RuntimeError(f"CHROMEDRIVER_PATH does not exist: {candidate}")
        return str(candidate)
    if LOCAL_CHROMEDRIVER_PATH.is_file():
        return str(LOCAL_CHROMEDRIVER_PATH)
    found = shutil.which("chromedriver")
    if found:
        return found
    raise RuntimeError(
        "Local ChromeDriver not installed. Install the matching official driver at "
        f"{LOCAL_CHROMEDRIVER_PATH} or set CHROMEDRIVER_PATH. "
        "Automatic driver downloads are disabled; see CRAWL_OPTIMIZATION.md."
    )


def executable_version(path: str, *, windows_browser: bool = False) -> str:
    if windows_browser and os.name == "nt":
        literal = str(path).replace("'", "''")
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   f"(Get-Item -LiteralPath '{literal}').VersionInfo.ProductVersion"]
    else:
        command = [path, "--version"]
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(r"\b\d+\.\d+\.\d+\.\d+\b", result.stdout)
    if not match:
        raise RuntimeError(f"Unable to read executable version: {path}")
    return match.group()


def validate_chrome_driver_versions(browser_version: str, driver_version: str) -> None:
    # Chrome for Testing publishes matching drivers by MAJOR.MINOR.BUILD;
    # the final patch number may legitimately differ (e.g. .85 and .84).
    if browser_version.split(".")[:3] != driver_version.split(".")[:3]:
        raise RuntimeError(
            f"Chrome {browser_version} and ChromeDriver {driver_version} are incompatible. "
            "Update the local ChromeDriver to match Chrome; no download was attempted."
        )


def build_candidate_urls(country_code: str, asin: str) -> list[tuple[str, str]]:
    domain = get_site_domain(country_code)
    # A fresh browser profile can retain a global USD preference even on a
    # European Amazon host.  Request the site's currency explicitly so the
    # offer parser never has to guess which display preference was active.
    currency_code = next(iter(SITE_CURRENCY_CODES.get(country_code.upper(), ())), "")
    currency_query = f"?currency={currency_code}" if currency_code else ""
    # Pin Japanese labels even when the dedicated browser previously selected
    # Chinese/English; otherwise ratings can be mistaken for absent fields.
    if country_code.upper() == "J":
        currency_query += "&language=ja_JP"
    return [
        ("product-dp", f"https://{domain}/dp/{asin}{currency_query}"),
        ("product-gp", f"https://{domain}/gp/product/{asin}{currency_query}"),
        ("mobile-aw", f"https://{domain}/gp/aw/d/{asin}{currency_query}"),
        ("reviews", f"https://{domain}/product-reviews/{asin}{currency_query}"),
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
    country_code = country_from_url(url)
    if not url_matches_site(response.url, country_code):
        raise RuntimeError(
            f"Amazon site mismatch: expected {get_site_domain(country_code)}, got {response.url}"
        )
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
        # Do not ask this Windows curl build to decompress the response. Some
        # bundled libcurl versions reject --compressed before making a request.
        "-H",
        "Accept-Encoding: identity",
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


def site_debugger_addresses() -> dict[str, str]:
    raw = os.getenv("AMAZON_SELENIUM_SITE_DEBUGGERS", "").strip()
    if not raw:
        return {}
    addresses = json.loads(raw)
    if not isinstance(addresses, dict) or not addresses:
        raise ValueError("site debugger addresses must be a nonempty object")
    if any(country not in SITE_DOMAIN_MAP or not isinstance(address, str)
           or not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", address)
           or not 1 <= int(address.rsplit(":", 1)[1]) <= 65535
           for country, address in addresses.items()):
        raise ValueError("site debugger addresses must use valid countries and loopback ports")
    if len(set(addresses.values())) != len(addresses):
        raise ValueError("Each site must use a distinct browser debugger address")
    return addresses


def create_selenium_driver(locale: str, profile_key: str | None = None):
    if webdriver is None:
        raise RuntimeError("selenium webdriver unavailable")

    debugger_address = os.getenv("AMAZON_SELENIUM_DEBUGGER_ADDRESS", "").strip()
    site_addresses = site_debugger_addresses()
    if site_addresses:
        if profile_key not in site_addresses:
            raise ValueError(f"No dedicated browser for site {profile_key}")
        debugger_address = site_addresses[profile_key]
    profile_context = None
    profile_dir = None
    if not debugger_address:
        temp_root = Path(__file__).resolve().parent.parent / ".selenium_tmp"
        temp_root.mkdir(exist_ok=True)
        persistent_profile = os.getenv("AMAZON_SELENIUM_PROFILE_DIR", "").strip()
        if persistent_profile:
            if profile_key:
                persistent_profile = str(Path(persistent_profile) / "sites" / re.sub(r"[^A-Za-z0-9_-]", "_", profile_key))
            profile_dir = str(Path(persistent_profile).resolve())
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
        else:
            profile_context = tempfile.TemporaryDirectory(prefix="amazon_selenium_", dir=str(temp_root))
            profile_dir = profile_context.name

    try:
        browser = os.getenv("AMAZON_SELENIUM_BROWSER", "edge").strip().lower()
        driver_service = None
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
            chrome_binary = find_chrome_binary()
            if chrome_binary:
                options.binary_location = chrome_binary
            driver_binary = find_chromedriver_binary()
            driver_version = executable_version(driver_binary)
            if chrome_binary:
                validate_chrome_driver_versions(
                    executable_version(chrome_binary, windows_browser=True), driver_version,
                )
            driver_service = ChromeService(executable_path=driver_binary)
            driver_factory = webdriver.Chrome
        else:
            raise RuntimeError("selenium browser options unavailable")

        if debugger_address:
            # Attach to the user's already-running browser. Do not add a
            # profile, headless, or remote-debugging argument in this mode.
            options.add_experimental_option("debuggerAddress", debugger_address)
        else:
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

        driver = driver_factory(options=options, service=driver_service) if driver_service else driver_factory(options=options)
        page_timeout = float(os.getenv("AMAZON_SELENIUM_PAGE_TIMEOUT", "15"))
        driver.set_page_load_timeout(page_timeout)
        driver._codex_attached = bool(debugger_address)
        return driver, profile_context
    except Exception:
        if profile_context is not None:
            profile_context.cleanup()
        raise


def close_selenium_driver(driver, profile_context) -> None:
    try:
        if driver is not None:
            if getattr(driver, "_codex_attached", False):
                # Stop only our driver service, preserving the user's browser.
                driver.service.stop()
            else:
                driver.quit()
    finally:
        if profile_context is not None:
            profile_context.cleanup()


def fetch_selenium_html(
    url: str,
    locale: str,
    driver=None,
    country_code: str | None = None,
    require_location: bool = True,
) -> str:
    owns_driver = driver is None
    profile_context = None
    expected_country = country_code or country_from_url(url)
    if owns_driver:
        driver, profile_context = create_selenium_driver(locale)

    try:
        if owns_driver:
            if not set_delivery_location_selenium(driver, expected_country, locale):
                raise RuntimeError(
                    f"delivery postal verification failed for {get_site_domain(expected_country)} "
                    f"({get_postal_code(expected_country)})"
                )
        recoveries = 1 if env_flag("AMAZON_PRIMARY_ONLY") else 2
        for recovery in range(recoveries):
            try:
                driver.get(url)
            except TimeoutException:
                # Secondary resources can time out after the product loaded.
                driver.execute_script("window.stop()")
            html = _wait_for_complete_product_html(
                driver,
                expected_country,
                float(os.getenv("AMAZON_SELENIUM_WAIT_SECONDS", "3")),
            )
            if not url_matches_site(driver.current_url, expected_country):
                raise RuntimeError(
                    f"Amazon site mismatch: expected {get_site_domain(expected_country)}, "
                    f"got {driver.current_url}"
                )
            expected_asin = re.search(r"/(?:dp|gp/product|gp/aw/d|product-reviews)/([^/?]+)", url)
            actual_asin = re.search(r"/(?:dp|gp/product|gp/aw/d|product-reviews)/([^/?]+)", driver.current_url)
            if expected_asin and actual_asin and expected_asin[1].upper() != actual_asin[1].upper():
                raise RuntimeError("Amazon ASIN mismatch after redirect")
            identity = BeautifulSoup(html, "html.parser").select_one('input#ASIN, input[name="ASIN"]')
            if expected_asin and identity and identity.get("value") and str(identity["value"]).upper() != expected_asin[1].upper():
                raise RuntimeError("Amazon ASIN mismatch in product page")
            if is_blocked_html(html):
                raise RuntimeError("blocked by Amazon captcha or robot check")
            if is_product_link_unavailable_html(html):
                raise ProductLinkUnavailable(LINK_UNAVAILABLE_MESSAGE)
            if require_location and not _delivery_location_matches(driver, expected_country):
                if recovery + 1 < recoveries and set_delivery_location_selenium(driver, expected_country, locale):
                    continue
                raise RuntimeError(
                    f"delivery postal verification failed on product page for "
                    f"{get_site_domain(expected_country)} ({get_postal_code(expected_country)})"
                )
            if not has_product_page(BeautifulSoup(html, "html.parser")):
                raise RuntimeError("Amazon response has no product details")
            return html
    finally:
        if owns_driver:
            close_selenium_driver(driver, profile_context)


def _wait_for_complete_product_html(driver, country_code: str, timeout: float) -> str:
    """Wait for core verified metrics, but never wait beyond the old page grace period."""
    interval = 0.25
    elapsed = 0.0
    html = ""
    while True:
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        if has_product_page(soup):
            metrics = extract_metrics(html, country_code, offer_allowed=True)
            if metrics.is_complete():
                return html
        if elapsed >= max(timeout, 0.0):
            return html
        pause = min(interval, max(timeout, 0.0) - elapsed)
        if pause <= 0:
            return html
        time.sleep(pause)
        elapsed += pause


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
    hostname = (urlparse(url).hostname or "").casefold()
    normalized_host = hostname[4:] if hostname.startswith("www.") else hostname
    for country_code, domain in SITE_DOMAIN_MAP.items():
        normalized_domain = domain.casefold()
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]
        if normalized_host == normalized_domain:
            return country_code
    return "U"


def url_matches_site(url: str, country_code: str) -> bool:
    hostname = (urlparse(url).hostname or "").casefold()
    expected = get_site_domain(country_code).casefold()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if expected.startswith("www."):
        expected = expected[4:]
    return bool(hostname) and hostname == expected


def set_delivery_location_selenium(driver, country_code: str, locale: str) -> bool:
    """Set Amazon's delivery postal code inside the browser session.

    The delivery endpoint must be called from the matching Amazon domain so
    that its cookies are written to the same browser profile that will load
    the product.  Return only after the browser confirms a successful HTTP
    response; silently continuing here can produce a valid-looking but
    wrong-region price.
    """
    postal_code = get_postal_code(country_code)
    domain = get_site_domain(country_code)
    endpoint = f"https://{domain}/gp/delivery/ajax/address-change.html"
    driver.get(f"https://{domain}/")
    time.sleep(2)
    page_html = getattr(driver, 'page_source', '')
    if isinstance(page_html, str) and is_blocked_html(page_html):
        raise RuntimeError('blocked by Amazon captcha or robot check')
    _dismiss_cookie_consent(driver)
    time.sleep(1)
    if _delivery_location_matches(driver, country_code):
        return True

    # First try Amazon's own AJAX endpoint from the page context. Some Amazon
    # locales accept the request but do not update the visible header, so the
    # result is only trusted after the header verification below.
    try:
        script = """
            const [endpoint, postalCode] = arguments;
            const done = arguments[arguments.length - 1];
            const params = new URLSearchParams({
                locationType: "LOCATION_INPUT",
                zipCode: postalCode,
                storeContext: "generic",
                deviceType: "web",
                pageType: "Detail",
                actionSource: "glow"
            });
            fetch(endpoint, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest"
                },
                body: params.toString()
            }).then(response => {
                done(JSON.stringify({ok: response.ok, status: response.status}));
            }).catch(error => done(JSON.stringify({ok: false, error: String(error)})));
        """
        driver.set_script_timeout(20)
        raw_result = driver.execute_async_script(script, endpoint, postal_code)
        result = json.loads(raw_result or "{}")
        if result.get("ok"):
            driver.get(f"https://{domain}/")
            time.sleep(2)
            _dismiss_cookie_consent(driver)
            time.sleep(1)
            if _delivery_location_matches(driver, country_code):
                return True
    except Exception:
        pass

    # Fallback: use the same controls as a human browser session. Amazon uses
    # stable GLUX ids across its country domains, but the submit action is an
    # input nested inside a span and a second confirmation is required on DE/UK.
    def visible(selector: str):
        try:
            for node in driver.find_elements("css selector", selector):
                if node.is_displayed() and node.is_enabled():
                    return node
        except Exception:
            return None
        return None

    postal_input = visible(
        "#GLUXZipUpdateInput, #GLUXZipUpdateInput_0, input[name='zipCode'], "
        "input[id*='ZipUpdateInput'], input[aria-label*='postcode' i], "
        "input[placeholder*='postcode' i], input[placeholder*='zip' i]"
    )
    if postal_input is None:
        ingress = visible("#nav-global-location-popover-link") or visible("#glow-ingress-block")
        if ingress is None:
            raise RuntimeError(
                f"delivery postal setup failed for {domain} ({postal_code}): location control unavailable"
            )
        ingress.click()
        for _ in range(48):
            time.sleep(0.25)
            postal_input = visible(
                "#GLUXZipUpdateInput, #GLUXZipUpdateInput_0, input[name='zipCode'], "
                "input[id*='ZipUpdateInput'], input[aria-label*='postcode' i], "
                "input[placeholder*='postcode' i], input[placeholder*='zip' i]"
            )
            if postal_input is not None:
                break
    if postal_input is None:
        raise RuntimeError(
            f"delivery postal setup failed for {domain} ({postal_code}): postal input unavailable"
        )

    postal_inputs = [(postal_input, postal_code)]
    if country_code.upper() == "J":
        first = visible("#GLUXZipUpdateInput_0")
        second = visible("#GLUXZipUpdateInput_1")
        if first is not None and second is not None:
            digits = re.sub(r"\D", "", postal_code)
            if len(digits) != 7:
                raise ValueError("Japan delivery postal code must contain seven digits")
            postal_inputs = [(first, digits[:3]), (second, digits[3:])]

    for postal_node, postal_value in postal_inputs:
        try:
            postal_node.clear()
            postal_node.send_keys(postal_value)
            continue
        except Exception:
            pass
        filled = driver.execute_script(
            """
            const input = arguments[0];
            const value = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(input, value);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return input.value === value;
            """,
            postal_node,
            postal_value,
        )
        if not filled:
            raise RuntimeError(
                f"delivery postal setup failed for {domain} ({postal_code}): postal input unavailable"
            )

    submit = visible("#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate button")
    if submit is None:
        submit = visible(
            "input[name='gluxZipUpdate'], button[name='gluxZipUpdate'], "
            "input[type='submit'][value*='Update' i]"
        )
    if submit is None:
        raise RuntimeError(
            f"delivery postal setup failed for {domain} ({postal_code}): submit unavailable"
        )
    submit.click()

    # DE shows "Bestätigen" and UK shows "Continue" after applying the code.
    # Do not navigate away until this control has been accepted.
    for _ in range(24):
        time.sleep(0.25)
        confirm = (
            visible("#GLUXConfirmClose input, #GLUXConfirmClose button")
            or visible("#GLUXConfirmAddress input, #GLUXConfirmAddress button")
        )
        if confirm is not None:
            confirm.click()
            break

    for _ in range(30):
        time.sleep(0.25)
        if _delivery_location_matches(driver, country_code):
            return True

    # A refresh makes Amazon render the persisted delivery cookie in the header.
    driver.get(f"https://{domain}/")
    for _ in range(20):
        time.sleep(0.25)
        if _delivery_location_matches(driver, country_code):
            return True
    return False


def set_currency_preference_selenium(driver, country_code: str) -> bool:
    """Pin Amazon's display currency for the active site session."""
    currency_code = next(iter(SITE_CURRENCY_CODES.get(country_code.upper(), ())), "")
    domain = get_site_domain(country_code)
    cookie_domain = domain[4:] if domain.startswith("www.") else domain
    if not currency_code:
        return True
    try:
        if not url_matches_site(str(getattr(driver, "current_url", "")), country_code):
            driver.get(f"https://{domain}/")
        driver.add_cookie({
            "name": "i18n-prefs",
            "value": currency_code,
            "domain": f".{cookie_domain}",
            "path": "/",
        })
        return True
    except Exception:
        return False


def _dismiss_cookie_consent(driver) -> bool:
    """Dismiss Amazon's regional cookie wall before opening the address dialog."""
    selectors = (
        "#sp-cc-rejectall-link",
        "#sp-cc-accept",
        "input[name='accept']",
        "button[data-action-type='DISMISS']",
        "input[data-action-type='DISMISS']",
    )
    for _ in range(12):
        for selector in selectors:
            try:
                node = driver.find_element("css selector", selector)
                if not node.is_displayed() or not node.is_enabled():
                    continue
                try:
                    node.click()
                except Exception:
                    driver.execute_script(
                        """
                        const node = arguments[0];
                        node.scrollIntoView({block: 'center'});
                        if (node.form) {
                            if (node.form.requestSubmit) node.form.requestSubmit(node);
                            else node.form.submit();
                        } else {
                            node.click();
                        }
                        """,
                        node,
                    )
                for _ in range(12):
                    time.sleep(0.5)
                    try:
                        if not node.is_displayed():
                            return True
                    except Exception:
                        return True
                return True
            except Exception:
                continue
        time.sleep(0.5)
    try:
        return bool(driver.execute_script(
            """
            const selectors = [
                '#sp-cc-rejectall-link',
                '#sp-cc-accept',
                'input[name="accept"]',
                'button[data-action-type="DISMISS"]',
                'input[data-action-type="DISMISS"]',
                '[data-testid="拒绝"]',
                '[data-testid="reject"]'
            ];
            for (const selector of selectors) {
                const node = document.querySelector(selector);
                if (node && node.offsetParent !== null) {
                    if (node.form) node.form.submit(); else node.click();
                    return true;
                }
            }
            return false;
            """
        ))
    except Exception:
        return False


def _delivery_location_matches(driver, country_code: str) -> bool:
    """Verify the browser header reflects the configured site address."""
    postal_code = get_postal_code(country_code)
    try:
        if not url_matches_site(driver.current_url, country_code):
            return False
        text = driver.execute_script(
            """
            return [
                '#glow-ingress-line1',
                '#glow-ingress-line2',
                '#glow-ingress-block',
                '#nav-global-location-data-modal-action'
            ].map(selector => document.querySelector(selector)?.textContent || '')
             .join(' ');
            """
        )
        text = normalize_space(text) or ""
    except Exception:
        return False
    # Amazon inserts spacing and invisible directional characters into some
    # localized headers (for example "London SW1A1AA\u200c"). Postcodes are
    # alphanumeric, so compare their canonical forms.
    compact_text = re.sub(r"[^0-9a-z]+", "", text.casefold())
    compact_postal = re.sub(r"[^0-9a-z]+", "", postal_code.casefold())
    return compact_postal in compact_text


def _item_detail_rows(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Return label/value pairs from Amazon's Product information details."""
    containers = []
    seen_containers: set[int] = set()

    selectors = (
        "#productDetails_techSpec_section_1",
        "#productDetails_techSpec_section_2",
        "#productDetails_detailBullets_sections1",
        "#productDetails_detailBullets_sections2",
        "#productDetails_db_sections",
        "#prodDetails",
        "#productDetails",
        "#detailBullets_feature_div",
        "#detailBulletsWrapper_feature_div",
        '[data-feature-name="productDetails"]',
        '[data-feature-name="detailBullets"]',
    )
    for selector in selectors:
        for node in soup.select(selector):
            if id(node) not in seen_containers:
                containers.append(node)
                seen_containers.add(id(node))

    # Newer pages render Item details as an accordion instead of using the
    # legacy productDetails_* ids. Walk up from that heading to its section.
    heading_pattern = re.compile(DETAIL_HEADING_PATTERN, re.I)
    for heading in soup.find_all(string=lambda value: bool(value and heading_pattern.fullmatch(normalize_space(str(value)) or ""))):
        current = heading.parent
        for _ in range(8):
            if current is None:
                break
            if current.find("tr") or current.find("li"):
                if id(current) not in seen_containers:
                    containers.append(current)
                    seen_containers.add(id(current))
                break
            current = current.parent

    rows: list[tuple[str, str]] = []
    seen_rows: set[tuple[str, str]] = set()
    for container in containers:
        for row in container.select("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = normalize_space(cells[0].get_text(" ", strip=True))
            value = normalize_space(" ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
            if label and value:
                key = (label.casefold(), value)
                if key not in seen_rows:
                    rows.append((label, value))
                    seen_rows.add(key)

        for item in container.select("li"):
            label_node = item.find(["span", "b", "strong"], class_=re.compile(r"a-text-bold|label", re.I))
            label = normalize_space(label_node.get_text(" ", strip=True)) if label_node else None
            full_value = normalize_space(item.get_text(" ", strip=True))
            if not label or not full_value:
                continue
            value = normalize_space(full_value[len(label):].lstrip(" :："))
            if value:
                key = (label.casefold(), value)
                if key not in seen_rows:
                    rows.append((label, value))
                    seen_rows.add(key)

    return rows


def detect_item_detail_status(soup: BeautifulSoup) -> str:
    """Classify whether the fetched page exposes Product information details."""
    selectors = (
        "#productDetails_techSpec_section_1",
        "#productDetails_techSpec_section_2",
        "#productDetails_detailBullets_sections1",
        "#productDetails_detailBullets_sections2",
        "#productDetails_db_sections",
        "#prodDetails",
        "#productDetails",
        "#detailBullets_feature_div",
        "#detailBulletsWrapper_feature_div",
        '[data-feature-name="productDetails"]',
        '[data-feature-name="detailBullets"]',
    )
    has_container = any(soup.select_one(selector) is not None for selector in selectors)
    heading_pattern = re.compile(DETAIL_HEADING_PATTERN, re.I)
    if not has_container:
        for heading in soup.find_all(
            string=lambda value: bool(value and heading_pattern.fullmatch(normalize_space(str(value)) or ""))
        ):
            current = heading.parent
            for _ in range(8):
                if current is None:
                    break
                if current.find("tr") or current.find("li"):
                    has_container = True
                    break
                current = current.parent
            if has_container:
                break

    if not has_container:
        return DETAIL_EMPTY
    return DETAIL_FOUND if _item_detail_rows(soup) else DETAIL_EMPTY


def _item_detail_value(soup: BeautifulSoup, label_pattern: str) -> str | None:
    pattern = re.compile(label_pattern, re.I)
    for label, value in _item_detail_rows(soup):
        if pattern.search(label):
            return value
    return None


def parse_star(soup: BeautifulSoup, text: str) -> str | None:
    customer_reviews = _item_detail_value(
        soup,
        CUSTOMER_REVIEW_LABEL_PATTERN,
    )
    if customer_reviews:
        match = re.search(JAPANESE_STAR_VALUE_PATTERN, customer_reviews)
        if match:
            return match.group(1).replace(",", ".")
        match = re.search(STAR_VALUE_PATTERN, customer_reviews, re.I)
        if match:
            return match.group(1).replace(",", ".")

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
                match = re.search(JAPANESE_STAR_VALUE_PATTERN, value)
                if match:
                    return match.group(1).replace(",", ".")
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+out of 5 stars", value, re.I)
                if match:
                    return match.group(1)
                match = re.search(r"([0-5](?:[.,][0-9]+)?)", value)
                if match:
                    return match.group(1).replace(",", ".")
    match = re.search(JAPANESE_STAR_VALUE_PATTERN, text)
    if match:
        return match.group(1).replace(",", ".")
    match = re.search(STAR_VALUE_PATTERN, text, re.I)
    if match:
        return match.group(1).replace(",", ".")
    return None


def parse_buy_box(soup: BeautifulSoup, text: str) -> int | None:
    cart_selectors = (
        "#add-to-cart-button",
        "#add-to-cart-button-ubb",
        "input[name='submit.add-to-cart']",
        "input[name='submit.add-to-cart-ubb']",
        "button[name='submit.add-to-cart']",
    )
    cart_markers = (
        "add to cart", "add to basket", "agregar al carrito",
        "agregar a la cesta", "in den einkaufswagen", "in den einkaufskorb",
        "ajouter au panier",
        "aggiungi al carrello", "a\u00f1adir a la cesta",
        "カートに入れる",
    )
    for selector in cart_selectors:
        for node in soup.select(selector):
            value = normalize_space(node.get("value") or node.get_text(" ", strip=True)) or ""
            if value or selector.startswith("#add-to-cart-button"):
                return 1

    for node in soup.select("#buybox, #desktop_buybox, #buybox_feature_div, #rightCol"):
        lowered = (normalize_space(node.get_text(" ", strip=True)) or "").casefold()
        if any(marker in lowered for marker in cart_markers):
            return 1

    # Only an actual buy-box/availability container can establish a definitive
    # no-cart state. A sparse fallback URL may omit the whole buy box; leave it
    # unknown so a later candidate URL can still provide the real state.
    if soup.select_one("#buybox, #desktop_buybox, #buybox_feature_div, #rightCol, #availability"):
        return 0
    return None


def has_product_page(soup: BeautifulSoup) -> bool:
    """Whether this HTML is a real product detail page, not a sparse error page."""
    return bool(
        soup.select_one("#productTitle, #productDetails, #buybox, #rightCol, #centerCol")
        or soup.find(attrs={"id": re.compile(r"productDetails|buybox|centerCol|rightCol", re.I)})
    )


def parse_price(
    soup: BeautifulSoup,
    raw_html: str | None = None,
    currency: str = "",
    country_code: str = "",
    buy_box: int | None = None,
) -> str | None:
    # Price is valid only when the same rendered page has a confirmed cart.
    # Generic JSON-LD, alternate-offer and page-wide price nodes are excluded.
    if buy_box != 1:
        return None
    # Current Amazon layouts may render the visible price as separate whole
    # and fraction spans without an offscreen accessibility value.
    split_price_containers = (
        "#buybox",
        "#buybox_feature_div",
        "#corePrice_desktop",
        "#corePrice_feature_div",
        "[data-feature-name='corePrice']",
        "#corePriceDisplay_desktop_feature_div",
        "#corePriceDisplay_mobile_feature_div",
        "#apex_desktop",
    )
    for container_selector in split_price_containers:
        container = soup.select_one(container_selector)
        if not container:
            continue
        whole = container.select_one(".a-price-whole")
        fraction = container.select_one(".a-price-fraction")
        if not whole or (not fraction and country_code.upper() != "J"):
            continue
        whole_value = normalize_space(whole.get_text(" ", strip=True)) or ""
        fraction_value = (normalize_space(fraction.get_text(" ", strip=True)) or "") if fraction else ""
        whole_value = re.sub(r"[^0-9]", "", whole_value)
        fraction_value = re.sub(r"[^0-9]", "", fraction_value)
        if not whole_value or (not fraction_value and country_code.upper() != "J"):
            continue
        currency_node = container.select_one(".a-price-symbol")
        currency_value = (
            normalize_space(currency_node.get_text(" ", strip=True))
            if currency_node
            else get_currency(country_code)
        ) or ""
        if not price_matches_site(currency_value, country_code):
            continue
        separator = "," if country_code.upper() in {"G", "F", "I", "S"} else "."
        value = f"{whole_value}{separator}{fraction_value}" if fraction_value else whole_value
        if price_matches_site(value + currency_value, country_code):
            return format_site_price(value, currency)
    selectors = [
        # Current Buy Box offer only. Do not add page-wide .a-price selectors
        # here: they also match other sellers and recommendation widgets.
        "#corePrice_desktop .priceToPay .a-offscreen",
        "#corePrice_feature_div .priceToPay .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
        "#corePriceDisplay_mobile_feature_div .priceToPay .a-offscreen",
        "#apex_desktop .priceToPay .a-offscreen",
        "#buybox .priceToPay .a-offscreen",
        "#corePrice_desktop .apex-core-price-identifier .a-offscreen",
        "#corePrice_desktop .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePriceDisplay_mobile_feature_div .a-price .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        "#buybox .a-price .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value and re.search(r"\d", value) and price_matches_site(value, country_code):
                return format_site_price(value, currency)

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
                return format_site_price(value, currency)

    return None


def parse_deal(
    soup: BeautifulSoup,
    offer_verified: bool,
    country_code: str = "",
) -> list[dict[str, str]] | None:
    if not offer_verified:
        return None

    country = country_code.upper()
    discount_patterns = {
        "U": (r"save", r"savings", r"you save", r"off"),
        "M": (r"ahorra", r"descuento", r"de descuento", r"de rebaja"),
        "G": (r"sie sparen", r"sparen", r"rabatt"),
        "E": (r"save", r"savings", r"you save", r"off"),
        "C": (r"save", r"savings", r"you save", r"off", r"économisez", r"de réduction"),
        "F": (r"économisez", r"remise", r"de réduction", r"de remise"),
        "I": (r"risparmia", r"sconto", r"di sconto", r"di risparmio"),
        "S": (r"ahorra", r"descuento", r"de descuento", r"de rebaja"),
        "A": (r"save", r"savings", r"you save", r"off"),
    }
    coupon_markers_by_country = {
        "U": ("coupon",),
        "M": ("cupón", "cupon", "cupón de descuento"),
        "G": ("coupon", "gutschein", "rabattcoupon"),
        "E": ("coupon", "voucher"),
        "C": ("coupon", "voucher", "bon de réduction", "bon de reduction"),
        "F": ("coupon", "bon de réduction", "bon de reduction"),
        "I": ("coupon", "buono sconto"),
        "S": ("cupón", "cupon", "cupón de descuento"),
        "A": ("coupon",),
    }
    promotion_patterns_by_country = {
        "U": (r"limited time deal", r"lightning deal", r"deal of the day", r"best deal", r"promotion"),
        "M": (r"oferta por tiempo limitado", r"oferta limitada", r"oferta flash", r"promoci[oó]n", r"oferta del día", r"oferta del dia"),
        "G": (r"zeitlich begrenztes angebot", r"blitzangebot", r"angebot des tages", r"top-angebot", r"aktion", r"sonderangebot"),
        "E": (r"limited time deal", r"lightning deal", r"deal of the day", r"best deal", r"promotion"),
        "C": (r"limited time deal", r"lightning deal", r"deal of the day", r"promotion", r"offre à durée limitée", r"vente flash"),
        "F": (r"offre à durée limitée", r"offre du jour", r"vente flash", r"promotion", r"offre spéciale"),
        "I": (r"offerta a tempo limitato", r"offerta del giorno", r"offerta lampo", r"promozione", r"offerta speciale"),
        "S": (r"oferta por tiempo limitado", r"oferta limitada", r"oferta flash", r"promoci[oó]n", r"oferta del día", r"oferta del dia"),
        "A": (r"limited time deal", r"lightning deal", r"deal of the day", r"best deal", r"promotion"),
    }
    original_price_prefix = {
        "U": "Was", "M": "Antes", "G": "Statt", "E": "Was", "C": "Was",
        "F": "Ancien prix", "I": "Prezzo precedente", "S": "Antes", "A": "Was",
    }.get(country, "Was")

    roots = []
    for selector in (
        "#corePrice_desktop",
        "#corePrice_feature_div",
        "#corePriceDisplay_desktop_feature_div",
        "#corePriceDisplay_mobile_feature_div",
        "#buybox",
        "#buybox_feature_div",
        "#desktop_buybox_group_1",
        "#apex_desktop",
    ):
        roots.extend(soup.select(selector))
    unique_roots = []
    seen: set[int] = set()
    for root in roots:
        if id(root) not in seen:
            unique_roots.append(root)
            seen.add(id(root))
    text = normalize_space(" ".join(root.get_text(" ", strip=True) for root in unique_roots)) or ""
    lowered = text.casefold()
    deals: list[dict[str, str]] = []

    def add(kind: str, label: str) -> None:
        normalized = normalize_space(label) or ""
        if normalized and not any(item["type"] == kind and item["label"] == normalized for item in deals):
            deals.append({"type": kind, "label": normalized[:180]})

    localized_discount_terms = discount_patterns.get(country, discount_patterns["U"])
    discount_term_pattern = "|".join(localized_discount_terms)
    discount_match = re.search(
        rf"(?:-\s*\d{{1,3}}(?:[.,]\d+)?\s*%|"
        rf"(?:{discount_term_pattern})\s*[: ]*\s*\d{{1,3}}(?:[.,]\d+)?\s*%|"
        rf"\d{{1,3}}(?:[.,]\d+)?\s*%\s*(?:{discount_term_pattern}))",
        text,
        re.I,
    )
    if discount_match:
        add("price_discount", discount_match.group(0))
    else:
        strike = next(
            (node for root in unique_roots for node in root.select(
                '[data-a-strike="true"] .a-offscreen, .basisPrice .a-offscreen, '
                '.a-text-price .a-offscreen, .priceBlockStrikePriceString'
            ) if normalize_space(node.get_text(" ", strip=True))),
            None,
        )
        if strike:
            add("price_discount", f"{original_price_prefix} {normalize_space(strike.get_text(' ', strip=True))}")

    coupon_markers = coupon_markers_by_country.get(country, coupon_markers_by_country["U"])
    coupon_pos = min((lowered.find(marker) for marker in coupon_markers if lowered.find(marker) >= 0), default=-1)
    if coupon_pos >= 0:
        coupon_node_text = None
        marker_pattern = re.compile(
            "|".join(re.escape(marker) for marker in coupon_markers),
            re.I,
        )
        for root in unique_roots:
            candidates = root.find_all(attrs={"id": marker_pattern}) + root.find_all(attrs={"class": marker_pattern})
            for candidate in candidates:
                candidate_text = normalize_space(candidate.get_text(" ", strip=True)) or ""
                if candidate_text and marker_pattern.search(candidate_text):
                    coupon_node_text = candidate_text
                    break
            if coupon_node_text:
                break
        add("coupon", coupon_node_text or text[max(0, coupon_pos - 35): coupon_pos + 145])

    promotion_patterns = promotion_patterns_by_country.get(country, promotion_patterns_by_country["U"])
    for pattern in promotion_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            add("promotion", match.group(0))

    return deals


def parse_reviews(soup: BeautifulSoup, text: str) -> str | None:
    customer_reviews = _item_detail_value(
        soup,
        CUSTOMER_REVIEW_LABEL_PATTERN,
    )
    if customer_reviews:
        match = re.search(r"([\d,]+)\s*(?:個の評価|件の(?:評価|レビュー))", customer_reviews)
        if match:
            return normalize_digits(match.group(1))
        # Item details normally displays the count in parentheses: (183).
        match = re.search(r"\(([\d,.\s]+)\)", customer_reviews)
        if match:
            return normalize_digits(match.group(1))

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
                match = re.search(r"(\d[\d,.\s]*)", value)
                if match:
                    return normalize_digits(match.group(1))

    patterns = [
        r"([\d,]+)\s*(?:個の評価|件の(?:評価|レビュー))",
        r"([\d,]+)\s+customer reviews?",
        r"([\d,]+)\s+ratings?",
        r"([\d,]+)\s+global ratings?",
        r"([\d,]+)\s+review(?:s)?",
        r"([\d,.]+)\s+(?:opiniones?|valoraciones?)\s+(?:de\s+)?clientes?",
        r"\(([\d.]+)\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).replace(",", "")
    return None


def parse_bsr(text: str, raw_html: str | None = None, soup: BeautifulSoup | None = None) -> str | None:
    if soup is not None:
        item_details_bsr = _item_detail_value(
            soup,
            BSR_LABEL_PATTERN,
        )
        if item_details_bsr:
            chinese_ranks = re.findall(r"排第\s*([\d,]+)\s*名", item_details_bsr)
            if chinese_ranks:
                return normalize_digits(chinese_ranks[-1])
            japanese_ranks = re.findall(r"([\d,]+)\s*位", item_details_bsr)
            if japanese_ranks:
                return normalize_digits(japanese_ranks[-1])
            matches = re.findall(
                r"(?:#|Nr\.?|n[º°o]\.?|n\.)\s*([\d,.]+)|"
                r"(\d[\d,.]*)\s+(?:in|en|dans|di|de|im)\b",
                item_details_bsr, re.I,
            )
            if matches:
                # Amazon lists the broad category first and subcategories
                # after it. The last displayed rank is the small-category BSR.
                return normalize_digits(next(value for value in matches[-1] if value))

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
        "amazon bestseller-rang",
        "bestseller-rang",
        "clasificación en los más vendidos de amazon",
        "clasificacion en los mas vendidos de amazon",
        "ranking de los más vendidos",
        "ranking de los mas vendidos",
        "clasificación de los más vendidos",
        "clasificacion de los mas vendidos",
        "rang des meilleures ventes",
        "classifica nelle vendite",
        "classifica dei bestseller",
        "classificação dos mais vendidos",
        "ranking de ventas",
        "売れ筋ランキング",
        "ベストセラーランキング",
    )
    snippets: list[str] = []
    for marker in rank_labels:
        idx = lowered.find(marker)
        if idx >= 0:
            snippets.append(normalized[idx : idx + 1200])
    snippets.append(normalized)
    patterns = [
        r"(?:売れ筋ランキング|ベストセラーランキング)[^\d]{0,100}([\d,]+)\s*位",
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
        match = re.search(r"(?:残り\s*(\d+)\s*点|仅剩\s*(\d+)\s*件)", candidate)
        if match:
            return next(value for value in match.groups() if value is not None)
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
                "在庫切れ", "現在在庫切れ", "一時的に在庫切れ",
                "目前无货", "暂时无货", "现在无货",
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
                "在庫あり",
                "现在有货", "目前有货", "现货",
            )
        ):
            return "1"
    return None


def mark_detail_unavailable(metrics: CrawlMetric, soup: BeautifulSoup) -> CrawlMetric:
    """Mark metrics absent from a successfully loaded details section.

    Amazon often omits BSR, review count, or rating for products that have no
    such detail yet. Those omissions are terminal data, not transient fetch
    failures. Restrict the decision to labels found inside the product-detail
    rows so unrelated text elsewhere on the page cannot invent a value.
    """
    if metrics.detail_status != DETAIL_FOUND:
        return metrics
    labels = " ".join(label.casefold() for label, _ in _item_detail_rows(soup))
    unavailable_fields = {
        "star": CUSTOMER_REVIEW_LABEL_PATTERN,
        "reviews": CUSTOMER_REVIEW_LABEL_PATTERN,
        "bsr": BSR_LABEL_PATTERN,
    }
    for field_name, pattern in unavailable_fields.items():
        if getattr(metrics, field_name) is None and not re.search(pattern, labels, re.I):
            setattr(metrics, field_name, "无")
    return metrics


def _count_other_seller_sold_by(soup: BeautifulSoup) -> int | None:
    marker = re.compile(
        r"(?:other\s+sellers\s+on\s+amazon|otros\s+vendedores\s+en\s+amazon|"
        r"andere\s+verk[aä]ufer\s+bei\s+amazon|other\s+sellers|Amazonの他の出品者)", re.I
    )
    sold_marker = re.compile(r"(?:sold\s+by|vendido\s+por|verk[aä]ufer|vendu\s+par|販売元)", re.I)
    section = None
    for node in soup.find_all(string=marker):
        current = node.parent
        for _ in range(8):
            if current is None:
                break
            current_text = normalize_space(current.get_text(" ", strip=True)) or ""
            if marker.search(current_text) and sold_marker.search(current_text):
                section = current
                break
            current = current.parent
        if section is not None:
            break

    if section is None:
        return None

    # Each offer row is the authoritative seller entry. Exclude the pinned
    # Buy Box offer, which may also contain a Sold by label.
    offer_nodes = []
    for selector in ("#aod-offer-list .aod-offer", ".aod-offer", "#olpOfferList .olpOffer"):
        offer_nodes.extend(section.select(selector))
    unique_offers = []
    seen_offer_ids: set[int] = set()
    for offer in offer_nodes:
        if id(offer) not in seen_offer_ids:
            unique_offers.append(offer)
            seen_offer_ids.add(id(offer))
    sold_offers = [
        offer for offer in unique_offers
        if sold_marker.search(normalize_space(offer.get_text(" ", strip=True)) or "")
    ]
    if sold_offers:
        return len(sold_offers)

    # Some Amazon variants do not wrap offers in .aod-offer. Count visible
    # Sold by text nodes in the Other sellers section as a structural fallback.
    sold_by_nodes = section.find_all(string=sold_marker)
    if sold_by_nodes:
        return len(sold_by_nodes)
    return 0


def parse_follow_sale(soup: BeautifulSoup, text: str, raw_html: str | None = None) -> str | None:
    other_seller_count = _count_other_seller_sold_by(soup)
    if other_seller_count is not None:
        return str(other_seller_count)
    # Seller counts are valid only when the Other sellers section contains
    # identifiable Sold by/Vendido por/Verkäufer rows. Do not infer counts
    # from prices, offer summaries, or arbitrary page numbers.
    return None

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


def extract_metrics(
    html: str,
    country_code: str = "",
    offer_allowed: bool = False,
) -> CrawlMetric:
    if not html or not html.strip():
        return CrawlMetric(detail_status=DETAIL_UNKNOWN)
    soup = BeautifulSoup(html, "html.parser")
    text = normalize_space(soup.get_text(" ", strip=True)) or ""
    offer_verified = offer_allowed and has_product_page(soup)
    buy_box = parse_buy_box(soup, text) if offer_verified else None
    skip_deal = os.getenv("AMAZON_SKIP_DEAL", "0").lower() in {"1", "true", "yes", "on"}
    metrics = CrawlMetric(
        buy_box=buy_box,
        price=parse_price(
            soup,
            html,
            get_currency(country_code),
            country_code,
            buy_box=buy_box,
        ),
        star=parse_star(soup, text),
        reviews=parse_reviews(soup, text),
        bsr=parse_bsr(text, html, soup),
        inventory=parse_inventory(soup, text),
        follow_sale=parse_follow_sale(soup, text, html),
        deal=None if skip_deal else parse_deal(soup, offer_verified and buy_box == 1, country_code),
        detail_status=detect_item_detail_status(soup),
        offer_verified=offer_verified,
    )
    return mark_detail_unavailable(metrics, soup)


def crawl_item_metrics(
    country_code: str,
    asin: str,
    selenium_driver=None,
    selenium_location_state: dict[str, str] | None = None,
    request_session: requests.Session | None = None,
) -> tuple[CrawlMetric, list[CrawlAttempt]]:
    locale = get_locale(country_code)
    session = request_session or build_session(locale)
    attempts: list[CrawlAttempt] = []
    result = CrawlMetric()

    candidate_urls = build_candidate_urls(country_code, asin)
    selenium_only = (
        selenium_driver is not None
        and env_flag("AMAZON_SELENIUM_ONLY")
    )
    primary_only = env_flag("AMAZON_PRIMARY_ONLY")
    stop_on_block = env_flag("AMAZON_STOP_ON_BLOCK")
    try:
        fallback_limit = max(1, int(os.getenv("AMAZON_FALLBACK_LIMIT", "2")))
    except ValueError:
        fallback_limit = 2
    # Prefer a real browser for every product.  Amazon frequently serves a
    # captcha or a reduced page to requests/curl while the same URL is fully
    # available in a normal browser session. Compensation alone tries a small
    # number of alternate URLs, with a hard per-product attempt budget.
    enable_selenium = (
        selenium_driver is not None
        and os.getenv("AMAZON_ENABLE_SELENIUM", "").lower() in {"1", "true", "yes"}
    )
    # In primary mode a working browser is authoritative; if Selenium is
    # unavailable, retain one lightweight request so a headless deployment
    # still produces a useful checkpoint for compensation.
    lightweight_enabled = not selenium_only and not (primary_only and enable_selenium)
    # Lightweight paths only supply non-offer metrics, so postal setup on
    # their separate sessions cannot improve offer verification.
    budget = 1 if primary_only else (3 if stop_on_block else 12)
    if enable_selenium:
        expected_postal = get_postal_code(country_code)
        attached_browser = bool(getattr(selenium_driver, "_codex_attached", False))
        location_ready = bool(
            selenium_location_state is not None
            and selenium_location_state.get("country") == country_code
            and selenium_location_state.get("postal_code") == expected_postal
            and _delivery_location_matches(selenium_driver, country_code)
        )
        if not location_ready and attached_browser:
            # A manually configured browser may already show the requested
            # postcode. Reuse that verified state instead of reopening the
            # location dialog and disturbing the user's session.
            location_ready = _delivery_location_matches(selenium_driver, country_code)
            if location_ready and selenium_location_state is not None:
                selenium_location_state["country"] = country_code
                selenium_location_state["postal_code"] = expected_postal
        for location_try in range(1 if primary_only else 2):
            if location_ready:
                break
            try:
                location_ready = set_delivery_location_selenium(
                    selenium_driver,
                    country_code,
                    locale,
                )
                if not location_ready:
                    raise RuntimeError(
                        f"delivery postal verification failed for "
                        f"{get_site_domain(country_code)} ({expected_postal})"
                    )
                if selenium_location_state is not None:
                    selenium_location_state["country"] = country_code
                    selenium_location_state["postal_code"] = expected_postal
            except Exception as exc:
                location_ready = False
                if selenium_location_state is not None:
                    selenium_location_state.clear()
                attempts.append(
                    CrawlAttempt(
                        method="selenium:delivery-location",
                        url=f"https://{get_site_domain(country_code)}/",
                        success=False,
                        message=str(exc),
                    )
                )
                if is_selenium_session_error(exc):
                    raise SeleniumSessionError(str(exc), result, attempts) from exc
                if stop_on_block and is_hard_block_message(str(exc)):
                    return result, attempts
        if location_ready:
            currency_ready = bool(
                selenium_location_state is not None
                and selenium_location_state.get("currency_country") == country_code
            )
            if not currency_ready:
                currency_ready = set_currency_preference_selenium(selenium_driver, country_code)
                if currency_ready and selenium_location_state is not None:
                    selenium_location_state["currency_country"] = country_code
            if not currency_ready:
                attempts.append(CrawlAttempt(
                    method="selenium:currency-preference",
                    url=f"https://{get_site_domain(country_code)}/",
                    success=False,
                    message="unable to set site currency preference",
                ))
            selenium_urls = candidate_urls[:1] if (
                primary_only or env_flag("AMAZON_SELENIUM_FAST")
            ) else ([candidate_urls[0], candidate_urls[2]] if stop_on_block else candidate_urls)
            for url_name, url in selenium_urls:
                if result.is_complete() or len(attempts) >= budget:
                    break
                try:
                    html = fetch_selenium_html(
                        url,
                        locale,
                        driver=selenium_driver,
                        country_code=country_code,
                        require_location=True,
                    )
                    parsed = extract_metrics(html, country_code, offer_allowed=True)
                    result.merge(parsed)
                    attempts.append(
                        CrawlAttempt(
                            method=f"selenium:{url_name}",
                            url=url,
                            success=True,
                            message=f"filled={_filled_fields(parsed)}",
                        )
                    )
                except Exception as exc:
                    attempts.append(
                        CrawlAttempt(
                            method=f"selenium:{url_name}",
                            url=url,
                            success=False,
                            message=str(exc),
                        )
                    )
                    if isinstance(exc, ProductLinkUnavailable):
                        return result, attempts
                    if is_selenium_session_error(exc):
                        raise SeleniumSessionError(str(exc), result, attempts) from exc
                    if stop_on_block and is_hard_block_message(str(exc)):
                        return result, attempts
                    if stop_on_block and classify_failure(str(exc)) in {"delivery", "identity_or_currency"}:
                        return result, attempts

    if lightweight_enabled:
        lightweight_urls = candidate_urls[:fallback_limit]
        for url_name, url in lightweight_urls:
            if result.is_complete() or len(attempts) >= budget:
                break
            try:
                html = fetch_requests_html(session, url, locale)
                # Lightweight clients cannot reliably prove that Amazon kept
                # the requested delivery postcode. Use them for non-offer
                # metrics only; cart and price require browser verification.
                parsed = extract_metrics(html, country_code, offer_allowed=False)
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
                if is_product_link_unavailable_message(str(exc)):
                    return result, attempts
                if stop_on_block and is_hard_block_message(str(exc)):
                    return result, attempts

    if result.is_complete() or not lightweight_enabled:
        if result.buy_box != 1:
            result.price = None
        return result, attempts

    for url_name, url in candidate_urls[:fallback_limit]:
        if result.is_complete() or len(attempts) >= budget:
            break
        try:
            html = fetch_curl_html(url, locale)
            parsed = extract_metrics(html, country_code, offer_allowed=False)
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
            if is_product_link_unavailable_message(str(exc)):
                return result, attempts
            if stop_on_block and is_hard_block_message(str(exc)):
                break

    if result.buy_box != 1:
        result.price = None
    return result, attempts


def _filled_fields(metric: CrawlMetric) -> list[str]:
    fields = []
    for field_name in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "follow_sale", "deal"):
        if getattr(metric, field_name) is not None:
            fields.append(field_name)
    return fields


def fetch_targets(
    connection,
    data_id: int | None = None,
    data_ids: list[int] | None = None,
    asin_country: str | None = None,
    limit: int | None = None,
    exclude_activity: bool = False,
    exclude_broken_links: bool = False,
) -> list[dict[str, Any]]:
    sql = [
        "SELECT id, asin, country, asin_country, name",
        f"FROM {quote_identifier('amazon_data')}",
    ]
    where_parts: list[str] = ["status = 1"]
    params: list[Any] = []
    if exclude_broken_links:
        where_parts.append("COALESCE(label, '') <> %s")
        params.append('链接挂了')

    if data_id is not None:
        where_parts.append("id = %s")
        params.append(int(data_id))
    if data_ids is not None:
        if not data_ids:
            return []
        placeholders = ", ".join(["%s"] * len(data_ids))
        where_parts.append(f"id IN ({placeholders})")
        params.extend(int(item) for item in data_ids)
    if asin_country:
        where_parts.append("asin_country = %s")
        params.append(asin_country)
    if exclude_activity:
        where_parts.extend([
            "TRIM(COALESCE(ad_activity, '')) IN ('', '未开启', '否', '0')",
            "TRIM(COALESCE(deal, '')) IN ('', '未开启', '否', '0')",
            "TRIM(COALESCE(deal_date, '')) = ''",
            "TRIM(COALESCE(deal_end_date, '')) = ''",
        ])
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


def mark_product_link_unavailable(connection, data_id: int, asin: str, country_code: str) -> bool:
    """Tag only the exact active marketplace row verified as a missing page."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE `amazon_data` SET `label` = %s "
            "WHERE `id` = %s AND UPPER(`asin`) = UPPER(%s) AND `country` = %s AND `status` = 1",
            ('链接挂了', int(data_id), str(asin), str(country_code)),
        )
        changed = cursor.rowcount == 1
    if changed:
        connection.commit()
    return changed


def fetch_product_link_unavailable_ids(connection, data_ids: list[int]) -> set[int]:
    if not data_ids:
        return set()
    placeholders = ', '.join(['%s'] * len(data_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM `amazon_data` WHERE `id` IN ({placeholders}) AND `label` = %s",
            [*map(int, data_ids), '链接挂了'],
        )
        return {int(row['id']) for row in cursor.fetchall()}


def fetch_completed_day_ids(connection, count_day: date, data_ids: list[int]) -> set[int]:
    """Return checkpoints that already passed the required-field contract."""
    if not data_ids:
        return set()
    placeholders = ", ".join(["%s"] * len(data_ids))
    sql = f"""
        SELECT data_id, buy_box, price, star, reviews, bsr
        FROM {quote_identifier('asin_day_data')}
        WHERE count_day = %s
          AND data_id IN ({placeholders})
          AND COALESCE(re_pull, 0) = 0
          AND buy_box IN (0, 1)
          AND star IS NOT NULL AND star <> ''
          AND reviews IS NOT NULL AND reviews <> ''
          AND bsr IS NOT NULL AND bsr <> ''
          AND (buy_box = 0 OR (price IS NOT NULL AND price <> ''))
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [count_day, *data_ids])
        rows = list(cursor.fetchall())
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, country FROM `amazon_data` WHERE id IN ({placeholders})",
            data_ids,
        )
        countries = {int(row["id"]): str(row["country"] or "U") for row in cursor.fetchall()}
    completed = {
        int(row["data_id"]) for row in rows
        if CrawlMetric(**{key: row.get(key) for key in ("buy_box", "price", "star", "reviews", "bsr")}).is_complete()
        and (str(row["buy_box"]) == "0" or price_matches_site(str(row["price"]), countries.get(int(row["data_id"]), "U")))
    }
    return completed | fetch_product_link_unavailable_ids(connection, data_ids)


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


def ensure_re_pull_column(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'asin_day_data'
              AND COLUMN_NAME = 're_pull'
            """
        )
        if int(cursor.fetchone()["total"]) == 0:
            cursor.execute(
                "ALTER TABLE `asin_day_data` ADD COLUMN `re_pull` TINYINT NOT NULL DEFAULT 0"
            )
            connection.commit()


def metric_needs_repull(metrics: CrawlMetric) -> bool:
    return not metrics.is_complete()


def upsert_day_record(
    connection,
    data_id: int,
    asin_country: str,
    name: str | None,
    metrics: CrawlMetric,
    count_day: date,
    fill_missing_only: bool = False,
    finalize_repull: bool = False,
    country_code: str = "",
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
        "deal": json.dumps(metrics.deal, ensure_ascii=False) if metrics.deal is not None else None,
        "re_pull": int(metric_needs_repull(metrics)),
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
        invalid_stored_price = bool(
            existing_values and existing_values.get("price") and country_code
            and not price_matches_site(str(existing_values["price"]), country_code)
        )
        updates = {"price": None} if invalid_stored_price else {}
        for column, value in values.items():
            if column in {"data_id", "count_day"}:
                continue

            # Only a verified offer may clear a stored price. Compensation
            # preserves a previously confirmed cart and its price together.
            clear_unverified_price = (
                column == "price"
                and value is None
                and metrics.offer_verified
                and (
                    not fill_missing_only
                    or str(existing_values.get("buy_box") or "0").strip() != "1"
                )
            )
            if value is None and not clear_unverified_price:
                continue
            if (
                clear_unverified_price
                or column == "re_pull"
                or not fill_missing_only
                or existing_values.get(column) in (None, "", "无")
                or (column == "buy_box" and value == 1 and metrics.offer_verified)
                or (column == "price" and invalid_stored_price)
            ):
                updates[column] = value
        if fill_missing_only:
            effective_values = {**existing_values, **updates}
            persisted_metrics = CrawlMetric(**{
                key: effective_values.get(key)
                for key in ("buy_box", "price", "star", "reviews", "bsr")
            })
            updates["re_pull"] = int(metric_needs_repull(persisted_metrics))
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
        for column in ("buy_box", "price", "star", "reviews", "bsr", "inventory", "re_pull")
    ):
        return "skipped"

    columns = ", ".join(quote_identifier(column) for column in insert_values)
    placeholders = ", ".join(["%s"] * len(insert_values))
    sql = f"INSERT INTO {quote_identifier('asin_day_data')} ({columns}) VALUES ({placeholders})"
    with connection.cursor() as cursor:
        cursor.execute(sql, list(insert_values.values()))
    connection.commit()
    return "inserted"


_DIAGNOSTIC_LOCK = Lock()


def write_crawl_diagnostic(record: CrawlRecord) -> None:
    entry = {"timestamp": datetime.now().astimezone().isoformat(), **record.to_dict()}
    entry["needs_repull"] = metric_needs_repull(record.metrics)
    try:
        log_path = Path(__file__).resolve().parent.parent / "crawl_attempts.jsonl"
        with _DIAGNOSTIC_LOCK, log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logging.getLogger(__name__).exception("Could not save crawl diagnostic")


def crawl_amazon_day_data(
    connection: pymysql.connections.Connection,
    data_id: int | None = None,
    data_ids: list[int] | None = None,
    asin_country: str | None = None,
    limit: int | None = None,
    fill_missing_only: bool = False,
    finalize_repull: bool = False,
    resume: bool = False,
    workers: int = 1,
    connection_factory=None,
    progress_callback=None,
    stop_event=None,
    profile_key: str | None = None,
    count_day: date | None = None,
    exclude_activity: bool = False,
    exclude_broken_links: bool = False,
) -> dict[str, Any]:
    ensure_re_pull_column(connection)
    targets = fetch_targets(connection, data_id=data_id, data_ids=data_ids, asin_country=asin_country,
                            limit=limit, exclude_activity=exclude_activity,
                            exclude_broken_links=exclude_broken_links)
    # Keep one marketplace together so the shared browser can reuse its
    # verified delivery location and currency settings across adjacent items.
    targets.sort(key=lambda target: (str(target.get("country") or "U"), int(target["id"])))
    today = count_day or date.today()
    resumed_count = 0
    if resume:
        completed_ids = fetch_completed_day_ids(connection, today, [int(target["id"]) for target in targets])
        if completed_ids:
            targets = [target for target in targets if int(target["id"]) not in completed_ids]
            resumed_count = len(completed_ids)
    # One browser/DB connection per marketplace, at most two marketplaces at
    # once. Never share a PyMySQL connection or browser across worker threads.
    workers = min(2, max(1, int(workers)))
    if os.getenv("AMAZON_SELENIUM_DEBUGGER_ADDRESS", "").strip():
        workers = 1
    dedicated_debuggers = site_debugger_addresses()
    if dedicated_debuggers and any(str(t.get("country") or "U") not in dedicated_debuggers for t in targets):
        raise ValueError("Missing dedicated browser for a selected site")
    if (workers > 1 or (dedicated_debuggers and not profile_key)) and connection_factory and targets:
        groups: dict[str, list[int]] = {}
        for target in targets:
            groups.setdefault(str(target.get("country") or "U"), []).append(int(target["id"]))
        def run_site(country, ids):
            with connection_factory() as site_connection:
                return crawl_amazon_day_data(
                    site_connection, data_ids=ids, fill_missing_only=fill_missing_only,
                    finalize_repull=False, resume=False, workers=1,
                    progress_callback=progress_callback, stop_event=stop_event,
                    profile_key=country, count_day=today,
                )
        combined = {"stats": {"resumed": resumed_count}, "items": []}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="amazon-site") as pool:
            futures = {pool.submit(run_site, country, ids): ids
                       for country, ids in sorted(groups.items(), key=lambda entry: -len(entry[1]))}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    ids = futures[future]
                    result = {"stats": {"total_targets": len(ids), "failed": len(ids)},
                              "items": [{"data_id": target_id, "db_action": "failed", "error": str(exc)} for target_id in ids]}
                for key, value in result["stats"].items():
                    combined["stats"][key] = combined["stats"].get(key, 0) + value
                combined["items"].extend(result["items"])
        return combined
    items: list[dict[str, Any]] = []
    stats = {
        "total_targets": len(targets),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
        "link_unavailable": 0,
        "resumed": resumed_count,
    }

    selenium_enabled = os.getenv("AMAZON_ENABLE_SELENIUM", "").lower() in {"1", "true", "yes"}
    selenium_driver = None
    profile_context = None
    selenium_location_state: dict[str, str] = {}
    request_sessions: dict[str, requests.Session] = {}
    try:
        if selenium_enabled and targets and not (stop_event and stop_event.is_set()):
            selenium_driver, profile_context = (
                create_selenium_driver("en-US,en;q=0.9", profile_key=profile_key)
                if profile_key else create_selenium_driver("en-US,en;q=0.9")
            )
        for target in targets:
            if stop_event and stop_event.is_set():
                break
            record = CrawlRecord(
                data_id=int(target["id"]),
                asin=str(target["asin"]),
                asin_country=str(target["asin_country"] or ""),
                country_code=str(target["country"] or "U"),
                count_day=today,
                name=str(target["name"] or "") or None,
            )

            try:
                recovery_round = 0
                while True:
                    try:
                        request_session = request_sessions.get(record.country_code)
                        if request_session is None:
                            request_session = build_session(get_locale(record.country_code))
                            request_sessions[record.country_code] = request_session
                        metrics, attempts = crawl_item_metrics(
                            record.country_code,
                            record.asin,
                            selenium_driver=selenium_driver,
                            selenium_location_state=selenium_location_state,
                            request_session=request_session,
                        )
                        record.metrics = metrics
                        record.attempts.extend(attempts)
                        break
                    except SeleniumSessionError as exc:
                        record.metrics = exc.metrics
                        record.attempts.extend(exc.attempts)
                        if not selenium_enabled or recovery_round >= 1:
                            raise
                        recovery_round += 1
                        try:
                            close_selenium_driver(selenium_driver, profile_context)
                            selenium_driver, profile_context = (
                                create_selenium_driver("en-US,en;q=0.9", profile_key=profile_key)
                                if profile_key else create_selenium_driver("en-US,en;q=0.9")
                            )
                            selenium_location_state.clear()
                            record.attempts.append(
                                CrawlAttempt(
                                    method="selenium:session-recovery",
                                    url=f"https://{get_site_domain(record.country_code)}/",
                                    success=True,
                                    message="recreated browser session and retrying item",
                                )
                            )
                        except Exception as recovery_exc:
                            record.attempts.append(
                                CrawlAttempt(
                                    method="selenium:session-recovery",
                                    url=f"https://{get_site_domain(record.country_code)}/",
                                    success=False,
                                    message=str(recovery_exc),
                                )
                            )
                            raise SeleniumSessionError(
                                f"browser session recovery failed: {recovery_exc}",
                                record.metrics,
                                record.attempts,
                            ) from recovery_exc

                link_unavailable = any(
                    is_product_link_unavailable_message(attempt.message)
                    for attempt in record.attempts
                )
                if link_unavailable:
                    mark_product_link_unavailable(
                        connection, record.data_id, record.asin, record.country_code,
                    )
                action = upsert_day_record(
                    connection,
                    record.data_id,
                    record.asin_country,
                    record.name,
                    record.metrics,
                    today,
                    fill_missing_only=fill_missing_only,
                    finalize_repull=finalize_repull,
                    country_code=record.country_code,
                )
                record.db_action = "link_unavailable" if link_unavailable else action
                if link_unavailable:
                    record.error = LINK_UNAVAILABLE_MESSAGE
                    stats["link_unavailable"] += 1
                if action in stats:
                    stats[action] += 1
                if (not link_unavailable and not record.metrics.has_any_value()
                        and record.metrics.detail_status == DETAIL_UNKNOWN):
                    record.error = "no metrics captured"
            except Exception as exc:
                record.db_action = "failed"
                record.error = str(exc)
                stats["failed"] += 1

            items.append(record.to_dict())
            write_crawl_diagnostic(record)
            if progress_callback:
                progress_callback(record.to_dict())
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
    finally:
        for session in request_sessions.values():
            session.close()
        close_selenium_driver(selenium_driver, profile_context)

    return {"stats": stats, "items": items}
