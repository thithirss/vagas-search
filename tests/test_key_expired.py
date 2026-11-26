"""Chave da OpenAI expirada (19–25/11): vira 'sem IA' na hora e a fila parada não segura a busca."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vagas import cli, llm
from vagas.budget import AIKeyInvalid, BudgetExceeded
from vagas.config import LLMCfg
from vagas.db import DB, Job

EXPIRED = "Error code: 401 - Your API key has expired. Create a new API key to continue."


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        raise RuntimeError(EXPIRED)


class KeyTests(unittest.TestCase):
    def test_detects_auth_errors_only(self):
        self.assertTrue(llm._key_invalid(EXPIRED))
        self.assertTrue(llm._key_invalid("Error code: 401 - {'code': 'invalid_api_key'}"))
        self.assertFalse(llm._key_invalid("Error code: 429 - rate limit"))
        self.assertFalse(llm._key_invalid("Connection error."))

    def test_expired_key_is_budget_path_without_retries(self):
        self.assertTrue(issubclass(AIKeyInvalid, BudgetExceeded))   # quem já trata o teto cai no caminho sem IA
        c = llm.LLM.__new__(llm.LLM)
        c.cfg, c.db, c.budget, c.client = LLMCfg(), None, None, FakeClient()
        with patch.object(llm, "_alert_key") as alert, self.assertRaises(AIKeyInvalid):
            c.json(model="gpt-4.1-mini", system="s", user="u", purpose="classify")
        self.assertEqual(c.client.calls, 1)
        alert.assert_called_once()


class QueueStateTests(unittest.TestCase):
    def test_run_command_still_registered(self):
        # 25/11: a função auxiliar entrou entre o @app.command() e o run, e o painel recebeu "No such command 'run'"
        names = {c.name or c.callback.__name__ for c in cli.app.registered_commands}
        self.assertIn("run", names)
        self.assertNotIn("_queue_state", names)

    def test_same_jobs_back_in_queue_is_no_progress(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        db = DB(Path(tmp.name) / "v.db")
        self.addCleanup(db.conn.close)
        db.upsert_job(Job(id="linkedin:1", source="linkedin", external_id="1", url="u", title="Dev", company="C"))
        db.set_status("linkedin:1", "queued", "classificação falhou: 401")
        a = cli._queue_state(db)
        db.set_status("linkedin:1", "queued", "classificação falhou: 401")   # a volta seguinte: mesma coisa
        self.assertEqual(cli._queue_state(db), a)
        db.set_status("linkedin:1", "skipped", "x")
        self.assertNotEqual(cli._queue_state(db), a)


if __name__ == "__main__":
    unittest.main()
