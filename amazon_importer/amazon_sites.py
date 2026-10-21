from __future__ import annotations


SITE_DOMAIN_MAP = {
    "U": "www.amazon.com",
    "M": "www.amazon.com.mx",
    "G": "www.amazon.de",
    "E": "www.amazon.co.uk",
    "C": "www.amazon.ca",
    "F": "www.amazon.fr",
    "I": "www.amazon.it",
    "S": "www.amazon.es",
    "A": "www.amazon.com.au",
    "J": "www.amazon.co.jp",
}


def get_site_domain(country_code: str) -> str:
    return SITE_DOMAIN_MAP.get(str(country_code or "").strip().upper(), "www.amazon.com")


def build_product_link(country_code: str, asin: str) -> str:
    normalized_asin = str(asin or "").strip()
    if not normalized_asin:
        return ""
    return f"https://{get_site_domain(country_code)}/dp/{normalized_asin}"
