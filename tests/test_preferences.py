import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from vagas import config, dashboard
from vagas.areas import area_of, track
from vagas.config import Settings
from vagas.db import Job


def job(title, location="Remoto"):
    return Job(id="x", source="linkedin", external_id="1", url="u", title=title, company="C", location=location)


class AreasTests(unittest.TestCase):
    def test_adjacent_areas_only_when_chosen(self):
        self.assertIsNone(area_of("Analista de QA", []))
        self.assertEqual(area_of("Analista de QA Pleno", ["qa"]), "qa")
        self.assertEqual(area_of("Consultor SAP MM", ["erp", "qa"]), "erp")
        self.assertEqual(area_of("Analista de Automação e IA", ["automacao_ia"]), "automacao_ia")
        self.assertIsNone(area_of("Técnico de Automação Industrial", ["automacao_ia"]))
        self.assertIsNone(area_of("Analista de Qualidade Industrial", ["qa"]))
        self.assertIsNone(area_of("Analista de Marketing e IA", ["automacao_ia"]))

    def test_chosen_area_ignores_not_dev_hard_reject(self):
        from vagas.pipeline import Pipeline
        tr = {"allow_non_dev": True}
        no_dev = {"is_dev_role": False, "hard_reject": True}
        self.assertTrue(Pipeline._track_overrides_reject(tr, {**no_dev, "reason": "Consultoria funcional SAP, não de desenvolvimento"}))
        self.assertFalse(Pipeline._track_overrides_reject(tr, {**no_dev, "reason": "Exige 5 anos de SAP e certificação"}))
        self.assertFalse(Pipeline._track_overrides_reject(tr, {**no_dev, "reason": "Engenharia de controle e automação industrial, não de desenvolvimento"}))
        self.assertFalse(Pipeline._track_overrides_reject(None, {**no_dev, "reason": "não de desenvolvimento"}))
        self.assertIsNone(area_of("BR - Intermediate Engineer, Controle & Automação", ["automacao_ia"]))

    def test_region_track(self):
        s = Settings.model_validate({"region_track": {"enabled": True, "name": "Campinas", "location": "Campinas",
                                                      "cities": ["Campinas", "Valinhos"]}})
        self.assertEqual(track(job("Analista de Suporte Técnico N1", "Campinas, SP"), {}, s)["name"], "Campinas")
        self.assertIsNone(track(job("Help Desk", "Belo Horizonte, MG"), {}, s))
        self.assertIsNone(track(job("Assistente Administrativo", "Valinhos"), {}, s))
        s.region_track.include_support = False
        self.assertIsNone(track(job("Técnico de Informática", "Valinhos"), {}, s))
        self.assertTrue(track(job("Desenvolvedor PHP", "Valinhos"), {}, s))
        s.region_track.enabled = False
        self.assertIsNone(track(job("Desenvolvedor PHP", "Valinhos"), {}, s))

    def test_region_searches_use_their_own_city(self):
        s = Settings.model_validate({"discovery": {"sources": ["linkedin"], "base_queries": ["dev"], "extra_queries": ["Claude Code"]},
                                     "region_track": {"enabled": True, "location": "Campinas, SP", "radius_km": 25,
                                                      "queries": ["suporte"], "sources": ["linkedin"]}})
        ss = s.all_searches()
        reg = [x for x in ss if x.location == "Campinas, SP"]
        self.assertEqual(len(reg), 1)
        self.assertTrue(reg[0].local and not reg[0].remote_only and reg[0].radius_km == 25)
        self.assertTrue(any(x.query == "Claude Code" and x.remote_only for x in ss))


class SavePrefsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "settings.yaml").write_text(yaml.safe_dump({"discovery": {"max_age_hours": 72},
                                                                 "filters": {"job_languages": ["pt-BR"]}}), encoding="utf-8")
        self.patches = [patch.object(config, "CONFIG_DIR", self.tmp), patch.object(config, "PREFS_PATH", self.tmp / "preferencias.yaml"),
                        patch.object(dashboard, "CONFIG_DIR", self.tmp)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def data(self):
        class D: pass
        d = D(); d.s = config.load_settings(); return d

    def test_saves_only_differences_and_reverts(self):
        r = dashboard.save_prefs(self.data(), {"discovery.max_age_hours": 168, "filters.adjacent_areas": ["qa", "dados"],
                                               "filters.accept_english": True, "limits.delay_between_applications": [30, 90],
                                               "discovery.extra_queries": "Claude Code\n\nclaude code\nn8n"})
        self.assertTrue(r["refilter"])
        prefs = yaml.safe_load((self.tmp / "preferencias.yaml").read_text(encoding="utf-8"))
        self.assertEqual(prefs["filters.adjacent_areas"], ["dados", "qa"])     # ordem do esquema
        self.assertEqual(prefs["filters.job_languages"], ["pt-BR", "en"])
        self.assertEqual(prefs["discovery.extra_queries"], ["Claude Code", "n8n"])   # sem vazio nem repetido
        s = config.load_settings()
        self.assertEqual(s.discovery.max_age_hours, 168)
        self.assertEqual(s.limits.delay_between_applications, (30, 90))
        dashboard.save_prefs(self.data(), {"discovery.max_age_hours": 72, "filters.adjacent_areas": [], "filters.accept_english": False,
                                           "limits.delay_between_applications": [45, 150], "discovery.extra_queries": []})
        self.assertFalse((self.tmp / "preferencias.yaml").exists())

    def test_rejects_invalid(self):
        for ch in ({"filters.allowed_seniorities": []}, {"limits.active_hours": ["22:00", "07:00"]},
                   {"limits.delay_after_failure": [10, 2]}, {"discovery.max_pages_per_search": 99}, {"nao.existe": 1}):
            with self.assertRaises(ValueError):
                dashboard.save_prefs(self.data(), ch)

    def test_every_schema_field_exists_in_settings(self):
        dump = Settings().model_dump()
        for key in dashboard._fields():
            self.assertIsNotNone(dashboard._dig(dump, key) if not key.startswith("limits.max_per_source_per_day.") else 0, key)


if __name__ == "__main__":
    unittest.main()
