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
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions
except Exception:  # pragma: no cover
    webdriver = None
    ChromeOptions = None
    EdgeOptions = None

from .importer import DEFAULT_DATABASE, connect, quote_identifier


DETAIL_FOUND = "detail_found"
DETAIL_EMPTY = "detail_empty"
DETAIL_UNKNOWN = "fetch_unknown"


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
            if getattr(self, field_name) is None:
                value = getattr(other, field_name)
                if value is not None:
                    setattr(self, field_name, value)
        self.merge_detail_status(other)

    def is_complete(self) -> bool:
        base_complete = all(
            getattr(self, field_name) is not None
            for field_name in ("buy_box", "star", "reviews", "bsr")
        )
        price_complete = self.buy_box == 0 or self.price is not None
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
    code = country_code.upper()
    lowered = price.lower().replace("\u00a0", " ")
    expected_codes = SITE_CURRENCY_CODES.get(code)
    if not expected_codes:
        return False
    expected_code = next(iter(expected_codes))

    # Explicit currency codes are unambiguous and must belong to the site.
    found_codes = set(re.findall(
        r"\b(?:USD|MXN|EUR|GBP|CAD|AUD|CNY|RMB|JPY|INR|BRL|KRW)\b",
        lowered.upper(),
    ))
    if found_codes and expected_code not in found_codes:
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
    if any(symbol in price for symbol in ("¥", "￥", "₹", "₽", "₩")):
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


def create_selenium_driver(locale: str):
    if webdriver is None:
        raise RuntimeError("selenium webdriver unavailable")

    debugger_address = os.getenv("AMAZON_SELENIUM_DEBUGGER_ADDRESS", "").strip()
    profile_context = None
    profile_dir = None
    if not debugger_address:
        temp_root = Path(__file__).resolve().parent.parent / ".selenium_tmp"
        temp_root.mkdir(exist_ok=True)
        persistent_profile = os.getenv("AMAZON_SELENIUM_PROFILE_DIR", "").strip()
        if persistent_profile:
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

        driver = driver_factory(options=options)
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
        if driver is not None and not getattr(driver, "_codex_attached", False):
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
        driver.get(url)
        wait_seconds = float(os.getenv("AMAZON_SELENIUM_WAIT_SECONDS", "3"))
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        if not url_matches_site(driver.current_url, expected_country):
            raise RuntimeError(
                f"Amazon site mismatch: expected {get_site_domain(expected_country)}, "
                f"got {driver.current_url}"
            )
        if require_location and not _delivery_location_matches(driver, expected_country):
            raise RuntimeError(
                f"delivery postal verification failed on product page for "
                f"{get_site_domain(expected_country)} ({get_postal_code(expected_country)})"
            )
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

    try:
        postal_input.clear()
        postal_input.send_keys(postal_code)
    except Exception:
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
            postal_input,
            postal_code,
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
    heading_pattern = re.compile(
        r"^(?:item\s+details|artikelangaben|produktdaten|product\s+information|"
        r"informaci[oó]n\s+del\s+producto|detalles\s+del\s+producto|"
        r"technical\s+details|additional\s+information|product\s+information)$",
        re.I,
    )
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
    heading_pattern = re.compile(
        r"^(?:item\s+details|artikelangaben|produktdaten|product\s+information|"
        r"informaci[oó]n\s+del\s+producto|detalles\s+del\s+producto|"
        r"technical\s+details|additional\s+information|product\s+information)$",
        re.I,
    )
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
        r"(?:customer\s+reviews?|durchschnittliche\s+kundenbewertung|kundenbewertung|"
        r"opini[oó]n\s+media\s+de\s+los\s+clientes|valoraci[oó]n\s+media\s+de\s+los\s+clientes)",
    )
    if customer_reviews:
        match = re.search(
            r"([0-9]+(?:[.,][0-9]+)?)\s+(?:out\s+of\s+5\s+stars|von\s+5\s+Sternen|"
            r"de\s+5\s+estrellas|sobre\s+5\s+estrellas)",
            customer_reviews,
            re.I,
        )
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
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+out of 5 stars", value, re.I)
                if match:
                    return match.group(1)
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
                if match:
                    return match.group(1)
    match = re.search(
        r"([0-9]+(?:[.,][0-9]+)?)\s+(?:out\s+of\s+5\s+stars|von\s+5\s+Sternen|"
        r"de\s+5\s+estrellas|sobre\s+5\s+estrellas)",
        text,
        re.I,
    )
    if match:
        return match.group(1)
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
        if not whole or not fraction:
            continue
        whole_value = normalize_space(whole.get_text(" ", strip=True)) or ""
        fraction_value = normalize_space(fraction.get_text(" ", strip=True)) or ""
        whole_value = re.sub(r"[^0-9]", "", whole_value)
        fraction_value = re.sub(r"[^0-9]", "", fraction_value)
        if not whole_value or not fraction_value:
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
        value = f"{whole_value}{separator}{fraction_value}"
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


def parse_reviews(soup: BeautifulSoup, text: str) -> str | None:
    customer_reviews = _item_detail_value(
        soup,
        r"(?:customer\s+reviews?|durchschnittliche\s+kundenbewertung|kundenbewertung|"
        r"opini[oó]n\s+media\s+de\s+los\s+clientes|valoraci[oó]n\s+media\s+de\s+los\s+clientes)",
    )
    if customer_reviews:
        # Item details normally displays the count in parentheses: (183).
        match = re.search(r"\(([\d,.]+)\)", customer_reviews)
        if match:
            return match.group(1).replace(",", "").replace(".", "")

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
            r"(?:best\s+sellers?\s+rank|bestseller[- ]rang|"
            r"clasificaci[oó]n\s+en\s+los\s+m[aá]s\s+vendidos|"
            r"ranking\s+de\s+los\s+m[aá]s\s+vendidos)",
        )
        if item_details_bsr:
            matches = re.findall(r"(?:#|Nr\.?|n[º°o])\s*([\d,.]+)", item_details_bsr, re.I)
            if matches:
                # Amazon lists the broad category first and subcategories
                # after it. The last displayed rank is the small-category BSR.
                return matches[-1].replace(",", "").replace(".", "")

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
        "star": (
            r"customer\s+reviews?|durchschnittliche\s+kundenbewertung|kundenbewertung|"
            r"opini[oó]n\s+media\s+de\s+los\s+clientes|valoraci[oó]n\s+media\s+de\s+los\s+clientes"
        ),
        "reviews": (
            r"customer\s+reviews?|durchschnittliche\s+kundenbewertung|kundenbewertung|"
            r"opini[oó]n\s+media\s+de\s+los\s+clientes|valoraci[oó]n\s+media\s+de\s+los\s+clientes"
        ),
        "bsr": (
            r"best\s+sellers?\s+rank|bestseller[- ]rang|"
            r"clasificaci[oó]n\s+en\s+los\s+m[aá]s\s+vendidos|"
            r"ranking\s+de\s+los\s+m[aá]s\s+vendidos"
        ),
    }
    for field_name, pattern in unavailable_fields.items():
        # A value found outside the detail rows is not trustworthy for this
        # decision. If the detail section has no matching label, explicitly
        # clear it and persist the terminal "无" marker.
        if not re.search(pattern, labels, re.I):
            setattr(metrics, field_name, "无")
    return metrics


def _count_other_seller_sold_by(soup: BeautifulSoup) -> int | None:
    marker = re.compile(
        r"(?:other\s+sellers\s+on\s+amazon|otros\s+vendedores\s+en\s+amazon|"
        r"andere\s+verk[aä]ufer\s+bei\s+amazon|other\s+sellers)", re.I
    )
    sold_marker = re.compile(r"(?:sold\s+by|vendido\s+por|verk[aä]ufer|vendu\s+par)", re.I)
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
        detail_status=detect_item_detail_status(soup),
        offer_verified=offer_verified,
    )
    return mark_detail_unavailable(metrics, soup)


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

    # Prefer a real browser for every product.  Amazon frequently serves a
    # captcha or a reduced page to requests/curl while the same URL is fully
    # available in a normal browser session.  Try all browser URL variants
    # first; only then use lightweight clients as fallbacks.
    enable_selenium = (
        selenium_driver is not None
        and os.getenv("AMAZON_ENABLE_SELENIUM", "").lower() in {"1", "true", "yes"}
    )
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
        if not location_ready:
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
        if location_ready:
            selenium_urls = candidate_urls
            if os.getenv("AMAZON_SELENIUM_FAST", "").lower() in {"1", "true", "yes"}:
                selenium_urls = candidate_urls[:1]
            for url_name, url in selenium_urls:
                if result.is_complete():
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

    if not selenium_only:
        for url_name, url in candidate_urls:
            if result.is_complete():
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

    if not result.is_complete() and not selenium_only:
        for url_name, url in candidate_urls:
            if result.is_complete():
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

    # Amazon may omit the cart control when the offer is unavailable. Treat
    # that state as an explicit no-cart result instead of exposing an unknown
    # value to the daily data view.
    if result.offer_verified and result.buy_box is None:
        result.buy_box = 0
    if result.buy_box != 1:
        result.price = None
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
    where_parts: list[str] = ["status = 1"]
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
    if all(getattr(metrics, field_name) == "无" for field_name in ("star", "reviews", "bsr")):
        return False
    return not metrics.is_complete() and metrics.detail_status != DETAIL_EMPTY


def upsert_day_record(
    connection,
    data_id: int,
    asin_country: str,
    name: str | None,
    metrics: CrawlMetric,
    count_day: date,
    fill_missing_only: bool = False,
    finalize_repull: bool = False,
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
        "re_pull": 0 if finalize_repull or not metric_needs_repull(metrics) else 1,
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
        updates = {}
        for column, value in values.items():
            if column in {"data_id", "count_day"}:
                continue

            # A full pull always replaces the day's price, including with NULL
            # when no verified cart price was found. During compensation, only
            # clear a stale price when the stored row is already no-cart.
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
                or existing_values.get(column) in (None, "")
            ):
                updates[column] = value
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


def crawl_amazon_day_data(
    connection: pymysql.connections.Connection,
    data_id: int | None = None,
    data_ids: list[int] | None = None,
    asin_country: str | None = None,
    limit: int | None = None,
    fill_missing_only: bool = False,
    finalize_repull: bool = False,
) -> dict[str, Any]:
    ensure_re_pull_column(connection)
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
                    action = upsert_day_record(
                        connection,
                        record.data_id,
                        record.asin_country,
                        record.name,
                        metrics,
                        today,
                        fill_missing_only=fill_missing_only,
                        finalize_repull=finalize_repull,
                    )
                    record.db_action = action
                    if action in stats:
                        stats[action] += 1
                    if not metrics.has_any_value() and metrics.detail_status == DETAIL_UNKNOWN:
                        record.error = "no metrics captured"
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
