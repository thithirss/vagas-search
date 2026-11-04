import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from vagas import inbox
from vagas.db import DB


class FakeIMAP:
    def __init__(self, ids):
        self.ids = ids

    def uid(self, cmd, uids, what):
        return "OK", [(f"{i} (X-GM-MSGID {m} UID {u})".encode(), b"") for i, (u, m) in enumerate(self.ids, 1)]


class InboxIntervalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "jobs.db")
        self.addCleanup(self.db.conn.close)

    def stamp(self, key, delta):
        self.db.kv_set(key, (datetime.now() + delta).isoformat(timespec="seconds"))

    def test_due_by_interval(self):
        self.assertTrue(inbox.due(self.db, 30))
        self.stamp("inbox_last_scan", -timedelta(minutes=10))
        self.assertFalse(inbox.due(self.db, 30))
        self.stamp("inbox_last_scan", -timedelta(minutes=31))
        self.assertTrue(inbox.due(self.db, 30))
        self.stamp("inbox_last_try", -timedelta(minutes=5))   # falha recente espera o intervalo
        self.assertFalse(inbox.due(self.db, 30))

    def test_future_timestamp_is_due(self):
        self.stamp("inbox_last_scan", timedelta(hours=3))
        self.assertTrue(inbox.due(self.db, 30))

    def test_only_unseen_messages_are_downloaded(self):
        inbox.save_response(self.db, msg_key="gm:111", received_at="", sender="", subject="", snippet="", status="negativo",
                            summary="", action="", link="", company="", job_id=None)
        inbox.mark_seen(self.db, "gm:222")
        out = inbox._unseen_uids(FakeIMAP([(1, 111), (2, 222), (3, 333)]), self.db, [b"1", b"2", b"3"])
        self.assertEqual(out, [b"3"])


if __name__ == "__main__":
    unittest.main()
