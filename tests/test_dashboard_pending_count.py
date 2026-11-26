import sqlite3
from types import SimpleNamespace

from vagas.dashboard import Data
from vagas.db import DB, Job


def test_pending_count_groups_questions_from_the_same_job():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, company TEXT);
        CREATE TABLE pending_questions (id INTEGER PRIMARY KEY, job_id TEXT, answered_at TEXT);
        INSERT INTO jobs VALUES ('job-1', 'Backend', 'Acme'), ('job-2', 'Backend', 'Acme');
        INSERT INTO pending_questions VALUES (1, 'job-1', NULL), (2, 'job-1', NULL),
                                             (3, 'job-2', NULL), (4, NULL, NULL);
    """)
    data = SimpleNamespace(db=SimpleNamespace(conn=conn))

    assert Data._pending_count(data) == 2


def test_answer_resolves_matching_questions_and_only_requeues_ready_jobs(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, reason TEXT, attempts INTEGER DEFAULT 0);
        CREATE TABLE pending_questions (
            id INTEGER PRIMARY KEY, job_id TEXT, question TEXT, options TEXT,
            answer TEXT, answered_at TEXT
        );
        INSERT INTO jobs (id, status, reason) VALUES
            ('job-1', 'pending_question', NULL),
            ('job-2', 'pending_question', NULL),
            ('job-3', 'pending_question', NULL);
        INSERT INTO pending_questions VALUES
            (1, 'job-1', 'Search', '["Gemini CLI","Codex"]', NULL, NULL),
            (2, 'job-2', 'Search', '["Gemini CLI","Codex"]', NULL, NULL),
            (3, 'job-2', 'Outra pergunta', '[]', NULL, NULL),
            (4, 'job-3', 'Search', '["React","Python"]', NULL, NULL);
    """)
    data    = object.__new__(Data)
    data.db = SimpleNamespace(conn=conn)
    data.s  = SimpleNamespace(answers_path=tmp_path / "answers.yaml")

    Data.answer(data, 1, "Gemini CLI")

    assert conn.execute("SELECT COUNT(*) FROM pending_questions WHERE answered_at IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT status FROM jobs WHERE id='job-1'").fetchone()[0] == "queued"
    assert conn.execute("SELECT status FROM jobs WHERE id='job-2'").fetchone()[0] == "pending_question"
    assert conn.execute("SELECT status FROM jobs WHERE id='job-3'").fetchone()[0] == "pending_question"


def test_queue_does_not_retry_a_job_with_an_unanswered_question(tmp_path):
    db = DB(tmp_path / "vagas.db")
    blocked = Job(
        id          = "blocked",
        source      = "test",
        external_id = "1",
        url         = "https://example.com/1",
        status      = "queued",
    )
    ready = Job(
        id          = "ready",
        source      = "test",
        external_id = "2",
        url         = "https://example.com/2",
        status      = "queued",
    )
    orphan = Job(
        id          = "orphan",
        source      = "test",
        external_id = "3",
        url         = "https://example.com/3",
        status      = "pending_question",
    )
    db.upsert_job(blocked)
    db.upsert_job(ready)
    db.upsert_job(orphan)
    db.add_pending_question(blocked.id, "Pergunta", "text", [], "")

    assert {job.id for job in db.queue()} == {ready.id, orphan.id}


def test_resolved_notice_waits_for_other_pending_questions(tmp_path):
    db = DB(tmp_path / "vagas.db")
    job = Job(
        id          = "job",
        source      = "test",
        external_id = "1",
        url         = "https://example.com/1",
        status      = "pending_question",
        attempts    = 3,
    )
    db.upsert_job(job)
    first = db.add_pending_question(job.id, "Faça login", "link", [], "")
    second = db.add_pending_question(job.id, "Pergunta restante", "text", [], "")
    data    = object.__new__(Data)
    data.db = db

    Data.resolve_pending(data, first)
    assert db.get_job(job.id).status == "pending_question"

    Data.resolve_pending(data, second)
    resolved = db.get_job(job.id)
    assert resolved.status == "queued"
    assert resolved.attempts == 0


def test_discarding_a_job_closes_all_of_its_pending_questions(tmp_path):
    db = DB(tmp_path / "vagas.db")
    job = Job(
        id          = "discarded-job",
        source      = "test",
        external_id = "4",
        url         = "https://example.com/4",
        status      = "pending_question",
    )
    db.upsert_job(job)
    first = db.add_pending_question(job.id, "Primeira", "text", [], "")
    db.add_pending_question(job.id, "Segunda", "text", [], "")
    data    = object.__new__(Data)
    data.db = db

    Data.dismiss_pending(data, first, skip_job=True)

    assert db.get_job(job.id).status == "skipped"
    assert not [row for row in db.pending_questions() if row["job_id"] == job.id]
