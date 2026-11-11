import email
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from vagas import fitcultural, inbox, nextsteps
from vagas.db import DB, Job

KEY = {
    "dimension_priority": ["cla", "adhocracia", "mercado", "hierarquia"],
    "questions": [
        {"n": 1, "use": [["O alcance de metas seja um esforço mais coletivo do que individual", "cla"],
                         ["Metas sejam definidas à medida em que as atividades são realizadas", "adhocracia"],
                         ["A remuneração seja de acordo com a minha produtividade", "mercado"],
                         ["A remuneração seja fixa e não baseada na produtividade", "hierarquia"]]},
    ],
}

# imita a tela do Fit Cultural da Gupy: 4 frases com alça de arrastar e caixinha de posição; "Próximo passo" troca a questão
FIXTURE = """<html><body><div id="app"></div><script>
const QS = [
 ["A remuneração seja fixa e não baseada na produtividade", "O alcance de metas seja um esforço mais coletivo do que individual",
  "A remuneração seja de acordo com a minha produtividade", "Metas sejam definidas à medida em que as atividades são realizadas"],
 ["Seja orientada a lucros e resultados para os acionistas", "Tenha rotina e procedimentos bem estruturados",
  "Valorize a harmonia e a união da equipe", "Abrace mudanças e inovação o tempo todo"],
];
let q = 0, order = [...QS[0]]; window.submitted = [];
function render() {
  const app = document.getElementById('app');
  if (q >= QS.length) { app.innerHTML = '<h1>Teste finalizado com sucesso!</h1>'; return; }
  app.innerHTML = `<p>QUESTÃO ${q+1} DE ${QS.length}</p><p>Eu prefiro trabalhar em uma organização que:</p>` +
    order.map((t, i) => `<div class="row"><button class="h"><span>drag_indicator</span></button><span>${t}</span>
      <select role="combobox">${[1,2,3,4].map(n => `<option ${n===i+1?'selected':''}>${n}</option>`).join('')}</select></div>`).join('') +
    `<button id="next">${q === QS.length-1 ? 'Finalizar' : 'Próximo passo'}</button>`;
  [...app.querySelectorAll('select')].forEach((s, i) => s.addEventListener('change', () => {
    const [it] = order.splice(i, 1); order.splice(+s.value - 1, 0, it); render(); }));
  document.getElementById('next').onclick = () => { window.submitted.push([...order]); q++; if (q < QS.length) order = [...QS[q]]; render(); };
}
render();
</script></body></html>"""


class FitCulturalTests(unittest.TestCase):
    def test_desired_order_uses_answer_key(self):
        rows = ["A remuneração seja fixa e não baseada na produtividade", "O alcance de metas seja um esforço mais coletivo do que individual",
                "A remuneração seja de acordo com a minha produtividade", "Metas sejam definidas à medida em que as atividades são realizadas"]
        self.assertEqual(fitcultural.desired_order(rows, KEY),
                         [t for t, _ in KEY["questions"][0]["use"]])

    def test_new_wording_falls_back_to_dimension(self):
        rows = ["Seja orientada a lucros e resultados para os acionistas", "Tenha rotina e procedimentos bem estruturados",
                "Valorize a harmonia e a união da equipe", "Abrace mudanças e inovação o tempo todo"]
        self.assertEqual(fitcultural.desired_order(rows, KEY),
                         ["Valorize a harmonia e a união da equipe", "Abrace mudanças e inovação o tempo todo",
                          "Seja orientada a lucros e resultados para os acionistas", "Tenha rotina e procedimentos bem estruturados"])

    def test_fresh_clone_uses_settings_priority(self):
        with patch.object(fitcultural, "KEY_PATH", fitcultural.KEY_PATH.with_name("nao-existe.json")):
            key = fitcultural.load_key()
        self.assertEqual(key["questions"], [])
        self.assertEqual(sorted(key["dimension_priority"]), ["adhocracia", "cla", "hierarquia", "mercado"])

    def test_keywords_classify_every_real_statement(self):
        if not fitcultural.KEY_PATH.exists():
            self.skipTest("gabarito pessoal não existe neste clone")
        key = json.loads(fitcultural.KEY_PATH.read_text(encoding="utf-8"))
        for q in key["questions"]:
            for text, dim in q["answered"]:
                self.assertEqual(fitcultural._dimension(text), dim, text)

    def test_behavioral_order_without_answer_key(self):
        key = {"dimension_priority": fitcultural.TESTS["comportamental"]["prio"], "questions": []}
        rows = ["Minhas decisões são espontâneas", "Gosto de passar meus conhecimentos sobre novas tecnologias para outras pessoas",
                "Tomo decisões de maneira racional"]
        self.assertEqual(fitcultural.desired_order(rows, key, 0, "comportamental"),
                         ["Tomo decisões de maneira racional",
                          "Gosto de passar meus conhecimentos sobre novas tecnologias para outras pessoas",
                          "Minhas decisões são espontâneas"])

    def test_behavioral_real_answer_key(self):
        path = fitcultural.TESTS["comportamental"]["key"]
        if not path.exists():
            self.skipTest("gabarito pessoal não existe neste clone")
        key = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(key["questions"]), 21)
        for q in key["questions"]:
            texts = [t for t, _ in q["use"]]
            self.assertTrue(all(fitcultural._dimension(t, "comportamental") for t in texts), texts)
            self.assertEqual(fitcultural.desired_order(list(reversed(texts)), key, q["n"], "comportamental"), texts)

    def test_unknown_statements_are_not_guessed(self):
        self.assertIsNone(fitcultural.desired_order(["a", "b", "c", "d"], KEY))

    def test_real_answer_key_covers_all_ten_questions(self):
        key = json.loads(fitcultural.KEY_PATH.read_text(encoding="utf-8")) if fitcultural.KEY_PATH.exists() else None
        if key is None:
            self.skipTest("gabarito pessoal não existe neste clone")
        for q in key["questions"]:
            texts = [t for t, _ in q["answered"]]
            self.assertEqual(fitcultural.desired_order(list(reversed(texts)), key), [t for t, _ in q["use"]])

    def test_fill_test_orders_and_finishes(self):
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            page = b.new_page(); page.set_content(FIXTURE)
            with patch.object(fitcultural, "load_key", return_value=KEY), patch("vagas.fitcultural.time.sleep"), patch.object(fitcultural, "_dump"):
                res = fitcultural.fill_test(page, log=lambda *a: None)
            sent = page.evaluate("window.submitted")
            b.close()
        self.assertEqual(res, "done")
        self.assertEqual(sent[0], [t for t, _ in KEY["questions"][0]["use"]])
        # questão 2 sem gabarito: ordem pela dimensão, com os dois primeiros alternados (variação moderada)
        self.assertEqual(set(sent[1][:2]), {"Valorize a harmonia e a união da equipe", "Abrace mudanças e inovação o tempo todo"})
        self.assertEqual(sent[1][3], "Tenha rotina e procedimentos bem estruturados")

    def test_unrecognized_question_stops_before_next(self):
        bad = FIXTURE.replace("Seja orientada a lucros e resultados para os acionistas", "Texto que não diz nada")
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            page = b.new_page(); page.set_content(bad)
            with patch.object(fitcultural, "load_key", return_value=KEY), patch("vagas.fitcultural.time.sleep"), patch.object(fitcultural, "_dump"):
                res = fitcultural.fill_test(page, log=lambda *a: None)
            sent = page.evaluate("window.submitted")
            b.close()
        self.assertTrue(res.startswith("failed: questão 2"))
        self.assertEqual(len(sent), 1)   # a 2ª questão não foi enviada


class InboxStepTests(unittest.TestCase):
    def test_kinds(self):
        k = inbox.kind_of
        self.assertEqual(k("Etapa Fit Cultural desbloqueada para a vaga Dev", "", "positivo"), "fit_cultural")
        self.assertEqual(k("Etapa Mapeamento Comportamental desbloqueada", "", "positivo"), "comportamental")
        self.assertEqual(k("Etapa 'Teste de Raciocínio Lógico' desbloqueada", "", "positivo"), "logica")
        self.assertEqual(k("Convite para entrevista técnica por vídeo", "", "positivo"), "entrevista")
        self.assertEqual(k("Inscrição recebida", "Você se inscreveu ontem, mas ainda não finalizou a sua candidatura.", "processo"), "finalizar")
        self.assertEqual(k("Vaga finalizada", "A vaga foi finalizada.", "negativo"), "")
        self.assertEqual(k("Boas vindas!", "Dúvidas? Fale com a gente.", "processo", "Ana <ana@iob.mailquickin.com>"), "")
        self.assertEqual(k("Sua candidatura", "Olá! Qual a sua disponibilidade para uma conversa na quinta?", "processo",
                           "Maria Souza <maria@empresa.com.br>"), "resposta")
        self.assertEqual(k("Sua candidatura", "Qual a sua disponibilidade?", "processo", "noreply@empresa.com"), "")

    def test_deadline(self):
        ref = datetime(2026, 11, 20)
        self.assertEqual(inbox.extract_deadline("Prazo até 24/11/2026 para gravar", ref), "2026-11-24")
        self.assertEqual(inbox.extract_deadline("disponível até 05/01", ref), "2027-01-05")
        self.assertEqual(inbox.extract_deadline("responda até o dia 3 de dezembro", ref), "2026-12-03")
        self.assertEqual(inbox.extract_deadline("faça nas próximas 48 horas", ref), "2026-11-22")
        self.assertEqual(inbox.extract_deadline("sem data nenhuma", ref), "")

    def test_links_unwrap_tracker_and_pick_by_kind(self):
        msg = email.message_from_string(
            'Content-Type: text/html\n\n<a href="https://fonts.googleapis.com/css?x">f</a>'
            '<a href="https://x.awstrack.me/L0/https:%2F%2Fapp.mindsight.com.br%2Fintegracoes%2Fats%3Fp%3D1/1/abc=">teste</a>'
            '<a href="https://email.gupy.com.br/c/eJx0abc">etapa</a><a href="https://www.instagram.com/x">ig</a>')
        links = inbox.links_of(msg)
        self.assertEqual(links[0], "https://app.mindsight.com.br/integracoes/ats?p=1")
        self.assertNotIn("https://www.instagram.com/x", links)
        self.assertTrue(inbox.pick_action_url(links, "logica").startswith("https://app.mindsight"))


class StepActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "jobs.db"); self.addCleanup(self.db.conn.close)

    def _job(self, jid, langs):
        j = Job(id=jid, source="gupy", external_id=jid, title="Dev", company="Montreal", url="https://x.gupy.io/job/1")
        j.status = "applied"; j.classification = {"required_languages": langs}
        self.db.upsert_job(j); self.db.save_job(j)
        self.db.add_application(j, "gupy", "gupy", None, [])
        return j

    def _resp(self, key, kind, job_id, **kw):
        inbox.save_response(self.db, msg_key=key, received_at="2026-11-10T10:00:00", sender=kw.get("sender", "x@gupy.io"),
                            subject=kw.get("subject", "s"), snippet="", status="processo", summary="", action="a", link="",
                            company="Montreal", job_id=job_id, kind=kind, reply_to=kw.get("reply_to", ""))
        return self.db.conn.execute("SELECT id FROM email_responses WHERE msg_key=?", (key,)).fetchone()[0]

    def test_refinish_requeues_and_bypasses_resend_lock_once(self):
        from vagas.config import Settings
        from vagas.pipeline import Pipeline
        self._job("gupy:1", [])
        self._resp("gm:1", "finalizar", "gupy:1"); self._resp("gm:2", "finalizar", "gupy:1")
        self.assertEqual(nextsteps.refinish(self.db, log=lambda *a: None), ["gupy:1"])
        self.assertEqual(self.db.get_job("gupy:1").status, "queued")
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM email_responses WHERE done=1").fetchone()[0], 2)
        p = Pipeline(Settings(), self.db, None, None, {}, None)
        self.assertIsNone(p._previous_application(self.db.get_job("gupy:1")))

    def test_refinish_respects_english_rule(self):
        self._job("gupy:2", ["inglês avançado"])
        self._resp("gm:3", "finalizar", "gupy:2")
        self.assertEqual(nextsteps.refinish(self.db, log=lambda *a: None), [])
        self.assertEqual(self.db.get_job("gupy:2").status, "applied")

    def test_send_reply_guards(self):
        rid = self._resp("gm:4", "resposta", None, reply_to="noreply@empresa.com")
        self.assertEqual(nextsteps.send_reply(self.db, rid), "sem rascunho para enviar")
        self.db.conn.execute("UPDATE email_responses SET reply_draft='Olá [PREENCHER: horário]' WHERE id=?", (rid,))
        self.assertIn("PREENCHER", nextsteps.send_reply(self.db, rid))
        self.db.conn.execute("UPDATE email_responses SET reply_draft='Olá, posso na quinta.' WHERE id=?", (rid,))
        self.assertIn("endereço de resposta inválido", nextsteps.send_reply(self.db, rid))

    def test_send_reply_uses_smtp_in_same_thread(self):
        rid = self._resp("gm:5", "resposta", None, reply_to="Maria <maria@empresa.com.br>", subject="Sua candidatura")
        self.db.conn.execute("UPDATE email_responses SET reply_draft='Olá Maria, posso na quinta. Thiago', message_id='<abc@x>' WHERE id=?", (rid,))
        sent = {}

        class FakeSMTP:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): pass
            def send_message(self, m): sent["m"] = m

        with patch("vagas.nextsteps.smtplib.SMTP_SSL", FakeSMTP), patch("vagas.inbox.credentials", return_value=("eu@gmail.com", "x")):
            self.assertEqual(nextsteps.send_reply(self.db, rid), "enviado")
        m = sent["m"]
        self.assertEqual(m["Subject"], "Re: Sua candidatura")
        self.assertEqual(m["In-Reply-To"], "<abc@x>")
        self.assertIn("maria@empresa.com.br", m["To"])
        self.assertEqual(nextsteps.send_reply(self.db, rid), "já foi enviado")


if __name__ == "__main__":
    unittest.main()
