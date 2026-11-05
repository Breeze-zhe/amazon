"""Dedicated, persistent Chrome sessions. Never solve an Amazon challenge."""
import json
from pathlib import Path
import os
import subprocess
import time

import requests
import websocket

from .day_crawler import (
    find_chrome_binary, get_site_domain, is_blocked_html,
    is_product_link_unavailable_html, url_matches_site,
)

BASE = Path(__file__).resolve().parent.parent
SITE_SESSIONS = {
    'C': (9223, '.amazon_chrome_manual_test'),
    'M': (9224, '.amazon_chrome_parallel_m'),
    'G': (9225, '.amazon_chrome_europe_G'),
    'E': (9226, '.amazon_chrome_europe_E'),
    'I': (9227, '.amazon_chrome_europe_I'),
    'F': (9228, '.amazon_chrome_europe_F'),
    'S': (9229, '.amazon_chrome_europe_S'),
    'J': (9230, '.amazon_chrome_japan_J'),
    'A': (9231, '.amazon_chrome_sequence_A'),
    'U': (9232, '.amazon_chrome_sequence_U'),
}
SITE_NAMES = dict(zip('CMGEIFSJAU', ['加拿大', '墨西哥', '德国', '英国', '意大利', '法国', '西班牙', '日本', '澳大利亚', '美国']))


def debugger_address(site):
    return f'127.0.0.1:{SITE_SESSIONS[site][0]}'


def page_status(session, site, start_browser=True):
    """Read only an existing matching site tab, starting its saved profile if absent."""
    port, profile = SITE_SESSIONS[site]
    endpoint = f'http://127.0.0.1:{port}/json/list'
    try:
        response = session.get(endpoint, timeout=3)
    except requests.ConnectionError:
        if not start_browser:
            raise RuntimeError(f'{site}: dedicated browser is closed')
        binary = find_chrome_binary()
        if not binary:
            raise RuntimeError('Local Chrome not found')
        startup = subprocess.STARTUPINFO() if os.name == 'nt' else None
        if startup:
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        subprocess.Popen([
            binary, '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}',
            f'--user-data-dir={BASE / profile}', '--no-first-run', '--no-default-browser-check',
            '--new-window', f'https://{get_site_domain(site)}/',
        ], startupinfo=startup)
        for attempt in range(20):
            time.sleep(1)
            try:
                response = session.get(endpoint, timeout=3)
                break
            except requests.ConnectionError:
                if attempt == 19:
                    raise
    response.raise_for_status()
    tabs = [t for t in response.json() if t.get('type') == 'page']
    if not tabs:
        raise RuntimeError(f'{site}: dedicated browser has no page tab')
    matching_tabs = [t for t in tabs if url_matches_site(t.get('url', ''), site)]
    # Prefer the site's existing tab; otherwise repair an ordinary stale tab.
    tab = (matching_tabs or tabs)[0]
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)
    redirected = False
    try:
        for attempt in range(20):
            request_id = attempt + 1
            ws.send(json.dumps({'id': request_id, 'method': 'Runtime.evaluate', 'params': {
                'expression': '({html:document.documentElement.outerHTML,url:location.href,ready:document.readyState,text:document.body?.innerText})',
                'returnByValue': True,
            }}))
            while True:
                message = json.loads(ws.recv())
                if message.get('id') == request_id:
                    value = message.get('result', {}).get('result', {}).get('value')
                    if not value:
                        raise RuntimeError(f'{site}: cannot inspect browser page')
                    break
            if not url_matches_site(value['url'], site):
                if is_blocked_html(value['html']):
                    return 'authentication_required'
                if redirected:
                    raise RuntimeError(
                        f'{site}: browser redirected to the wrong marketplace: {value["url"]}')
                navigation_id = 100 + attempt
                ws.send(json.dumps({'id': navigation_id, 'method': 'Page.navigate',
                                    'params': {'url': f'https://{get_site_domain(site)}/'}}))
                while True:
                    message = json.loads(ws.recv())
                    if message.get('id') == navigation_id:
                        break
                redirected = True
                time.sleep(0.5)
                continue
            # Missing-link pages can have an empty body (image-only errors).
            # They must not prevent proceeding to the next product.
            if is_product_link_unavailable_html(value['html']):
                return 'ready'
            if is_blocked_html(value['html']):
                return 'authentication_required'
            if value['ready'] == 'complete' and value.get('text', '').strip():
                return 'ready'
            time.sleep(0.5)
        raise RuntimeError(f'{site}: browser page not loaded')
    finally:
        ws.close()
