"""Busca em fatias e classificação em lotes (2026-11-18: 3,5 h de busca + 1 h de classificação sem nenhum envio)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vagas import pipeline as pl
from vagas.config import Search, Settings
from vagas.db import DB, Job


class FakeSource:
    needs_browser = False

    def __init__(self, clock):
        self.clock, self.calls = clock, []

    def search(self, browser, page, s, pages):
        self.calls.append(s.query)
        self.clock[0] += 300          # cada busca "leva" 5 min
        return []


class SliceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "jobs.db")
        self.addCleanup(self.db.conn.close)
        self.p = pl.Pipeline(Settings(), self.db, None, None, {}, None)
        self.clock = [1000.0]
        self.src = FakeSource(self.clock)
        searches = [Search(source="linkedin", query=f"q{i}") for i in range(10)]
        for pt in (patch.object(Settings, "all_searches", lambda s: searches),
                   patch.object(pl.Pipeline, "_source", lambda s, name: self.src),
                   patch.object(pl.time, "time", lambda: self.clock[0])):
            pt.start()
            self.addCleanup(pt.stop)

    def test_slices_continue_where_they_stopped(self):
        self.p.discover(max_minutes=12)                 # 3 buscas cabem (0, 5, 10 min; a 4ª começaria aos 15)
        self.assertEqual(self.src.calls, ["q0", "q1", "q2"])
        self.assertTrue(self.p.discovery_in_progress())
        self.assertIsNone(self.db.kv_get("last_discovery"))
        self.p.discover(max_minutes=12)
        self.assertEqual(self.src.calls[3:], ["q3", "q4", "q5"])
        self.p.discover(max_minutes=100)                # termina a volta
        self.assertEqual(self.src.calls[-1], "q9")
        self.assertFalse(self.p.discovery_in_progress())
        self.assertIsNotNone(self.db.kv_get("last_discovery"))

    def test_without_slice_runs_everything(self):
        self.p.discover()
        self.assertEqual(len(self.src.calls), 10)

    def test_promising_titles_first(self):
        self.assertTrue(self.p._promising(Job(id="a", source="linkedin", external_id="1", url="u", title="Desenvolvedor PHP Pleno")))
        self.assertFalse(self.p._promising(Job(id="b", source="linkedin", external_id="2", url="u", title="Camareira - Hotel")))


if __name__ == "__main__":
    unittest.main()
