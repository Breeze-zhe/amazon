import json
import unittest
from unittest.mock import Mock, patch

from amazon_importer import site_sessions


class FakeWebSocket:
    def __init__(self, values):
        self.values = iter(values)
        self.messages = []

    def send(self, payload):
        command = json.loads(payload)
        self.messages.append(command)
        if command['method'] == 'Runtime.evaluate':
            value = next(self.values)
            self.response = {'id': command['id'], 'result': {'result': {'value': value}}}
        else:
            self.response = {'id': command['id'], 'result': {}}

    def recv(self):
        return json.dumps(self.response)

    def close(self):
        pass


class SiteSessionTests(unittest.TestCase):
    def run_preflight(self, values):
        websocket = FakeWebSocket(values)
        session = Mock()
        session.get.return_value.json.return_value = [{
            'type': 'page', 'url': 'https://www.amazon.de/',
            'webSocketDebuggerUrl': 'ws://localhost/devtools/page/1',
        }]
        with patch.object(site_sessions.websocket, 'create_connection', return_value=websocket):
            result = site_sessions.page_status(session, 'U', start_browser=False)
        return result, websocket

    def test_repairs_wrong_marketplace_tab(self):
        result, websocket = self.run_preflight([
            {'url': 'https://www.amazon.de/', 'html': '<html><body>Home</body></html>',
             'ready': 'complete', 'text': 'Home'},
            {'url': 'https://www.amazon.com/', 'html': '<html><body>Home</body></html>',
             'ready': 'complete', 'text': 'Home'},
        ])

        self.assertEqual(result, 'ready')
        navigations = [m for m in websocket.messages if m['method'] == 'Page.navigate']
        self.assertEqual(navigations[0]['params']['url'], 'https://www.amazon.com/')

    def test_does_not_navigate_away_from_challenge(self):
        result, websocket = self.run_preflight([
            {'url': 'https://www.amazon.de/errors/validateCaptcha',
             'html': '<form action="/errors/validatecaptcha"><input id="captchacharacters"></form>',
             'ready': 'complete', 'text': 'Captcha'},
        ])

        self.assertEqual(result, 'authentication_required')
        self.assertFalse(any(m['method'] == 'Page.navigate' for m in websocket.messages))


if __name__ == '__main__':
    unittest.main()
