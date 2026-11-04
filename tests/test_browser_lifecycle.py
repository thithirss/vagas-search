import unittest
from unittest.mock import Mock
from vagas.browser import Browser
from vagas.config import Settings


class BrowserLifecycleTests(unittest.TestCase):
    def test_repeated_stop_closes_only_once(self):
        browser = Browser(Settings())
        context, playwright = Mock(), Mock()
        browser.context, browser._pw = context, playwright
        browser.stop()
        browser.stop()
        context.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_shutdown_after_closed_connection_keeps_cleanup_idempotent(self):
        browser = Browser(Settings())
        context, playwright = Mock(), Mock()
        context.close.side_effect = RuntimeError('Target page, context or browser has been closed')
        browser.context, browser._pw = context, playwright
        browser.stop()
        browser.stop()
        playwright.stop.assert_called_once()

    def test_unexpected_shutdown_errors_remain_visible(self):
        browser = Browser(Settings())
        browser.context, browser._pw = Mock(), Mock()
        browser.context.close.side_effect = RuntimeError('Falha inesperada')
        with self.assertRaisesRegex(RuntimeError, 'Falha inesperada'):
            browser.stop()
