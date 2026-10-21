"""Shared browser policy for all retry and compensation crawl paths."""


def compensation_browser_options(payload: dict, *, browser: str = "chrome") -> dict:
    """Return stable browser settings for compensation and fallback rounds.

    Keep an explicitly selected debugger/profile only when it belongs to the
    requested browser. Scheduled jobs can reuse their authenticated Chrome
    sessions, while the final Edge fallback must not attach to Chrome's port.
    """
    options = dict(payload)
    requested_browser = str(options.get("browser") or "").strip().lower()
    selected_browser = str(browser or "chrome").strip().lower()
    if requested_browser != selected_browser:
        for key in ("debugger_address", "debugger_addresses", "profile_dir"):
            options.pop(key, None)

    options.update(
        use_selenium=True,
        browser=selected_browser,
        headless=False,
        wait_seconds=5,
        page_timeout=25,
        selenium_only=True,
        workers=1,
    )
    return options
