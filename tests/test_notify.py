import json
import unittest
from unittest.mock import MagicMock, patch

from vagas.notify import notify


class TelegramNotificationTests(unittest.TestCase):
    @patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}, clear=True)
    @patch('vagas.notify.dotenv_values', return_value={'TELEGRAM_BOT_TOKEN': 'token-de-teste', 'TELEGRAM_CHAT_ID': '123'})
    @patch('vagas.notify.urllib.request.urlopen')
    def test_empty_inherited_variables_use_updated_file(self, urlopen, values):
        self.assertTrue(notify('Teste de integração'))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.telegram.org/bottoken-de-teste/sendMessage')
        self.assertEqual(json.loads(request.data)['chat_id'], '123')
        self.assertEqual(json.loads(request.data)['text'], 'Teste de integração')

    @patch.dict('os.environ', {}, clear=True)
    @patch('vagas.notify.dotenv_values', return_value={})
    @patch('vagas.notify.urllib.request.urlopen')
    def test_unconfigured_telegram_sends_nothing(self, urlopen, values):
        self.assertFalse(notify('Teste'))
        urlopen.assert_not_called()
