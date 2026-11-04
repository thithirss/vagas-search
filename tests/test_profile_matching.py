import copy
import json
import tempfile
import unittest
from pathlib import Path

from vagas.config import load_settings
from vagas.db import Job
from vagas.pipeline import Pipeline
from vagas.resume.profile import all_skills, load_profile, profile_for_forms, scale_profile_for_seniority, profile_fingerprint
from vagas.resume.library import _load
from vagas.resume.render import render_html
from vagas.resume.tailor import ensure_all_experiences, validate_resume


_SETTINGS = load_settings()


@unittest.skipUnless(_SETTINGS.profile_path.exists(), "precisa do seu config/profile.yaml (rode `vagas setup`)")
class ProfileMatchingTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()
        self.profile = load_profile(self.settings.profile_path)
        self.pipeline = Pipeline(self.settings, None, None, None, self.profile, None)

    def job(self, title, level, fit=90):
        return Job(id=title, source='linkedin', external_id=title, url='https://example.com',
                   title=title, discovered_at='2026-10-21T10:00:00',
                   classification={'seniority': level, 'fit_score': fit})

    def test_junior_precedes_better_scoring_mid(self):
        junior = self.job('Dev Júnior', 'junior', 75)
        mid = self.job('Dev Pleno', 'mid', 95)
        self.assertEqual(sorted([mid, junior], key=self.pipeline._queue_priority), [junior, mid])
        self.assertIsNone(self.pipeline._seniority_rejection(mid))
        self.assertIsNotNone(self.pipeline._seniority_rejection(self.job('Dev Sênior', 'senior')))

    def test_searches_prioritize_junior_and_cover_both_sources(self):
        searches = self.settings.all_searches()
        # a mesma frase pode existir como busca remota (Brasil) e local (região do usuário): a chave inclui a localização
        self.assertEqual(len(searches), len({(s.source, s.query, s.location) for s in searches}))
        junior = [i for i, s in enumerate(searches) if s.query.endswith('júnior')]
        mid = [i for i, s in enumerate(searches) if s.query.endswith('pleno')]
        self.assertLess(max(junior), min(mid))
        # 29/10: buscas por stack só no LinkedIn (no Indeed cada busca custa 30 s+ de navegador e rendia pouco);
        # as duas fontes continuam com as buscas gerais em todos os níveis
        for level in ['júnior', 'pleno']:
            self.assertTrue(any(s.source == 'linkedin' and s.query == f'desenvolvedor laravel {level}' for s in searches))
            for source in ['linkedin', 'indeed']:
                self.assertTrue(any(s.source == source and s.query == f'desenvolvedor full stack {level}' for s in searches))
        # regra de 2026-10-29: fora da região do usuário continua só remoto; idade = teto configurado (72h)
        self.assertTrue(all(s.remote_only for s in searches if not s.local))
        self.assertTrue(all(not s.remote_only for s in searches if s.local))
        age = f'{self.settings.discovery.max_age_hours}h'
        self.assertTrue(all(s.posted_within == age for s in searches if not s.airline))
        # trilha de companhia aérea (29/10): janela própria, porque vaga de TI em aérea é rara
        air = f'{min(self.settings.airlines.max_age_hours, 720)}h'
        self.assertTrue(all(s.posted_within == air for s in searches if s.airline))

    def test_two_xappa_roles_keep_their_titles_and_dates(self):
        originals = self.profile['experiences'][:2]
        resume = {'experiences': copy.deepcopy(originals), 'skills': {}, 'language': 'pt-BR'}
        validate_resume(resume, self.profile)
        self.assertEqual([(e['title'], e['start'], e['end']) for e in resume['experiences']],
                         [(e['title'], e['start'], e['end']) for e in originals])
        resume['experiences'] = resume['experiences'][:1]
        ensure_all_experiences(resume, self.profile, 'mid')
        self.assertEqual(len([e for e in resume['experiences'] if e['company'] == 'Xappa']), 2)

    def test_projects_render_from_profile_in_all_templates(self):
        profile = {'personal': {}, 'projects': [{'name': 'Projeto <real>', 'description': 'Descrição confirmada', 'stack': ['PHP']}]}
        resume = {'language': 'pt-BR', 'section_titles': {}, 'projects': [{'name': 'Inventado'}]}
        for template in ['modern', 'classic', 'resume']:
            with self.subTest(template=template):
                html = render_html(resume, profile, template=template)
                self.assertIn('Projeto &lt;real&gt;', html)
                self.assertNotIn('Inventado', html)
        self.assertIn('php', all_skills(profile))
        self.assertEqual(profile_for_forms(profile)['projects'], profile['projects'])

    def test_real_years_remain_equal_for_junior_and_mid(self):
        for level in ['junior', 'mid']:
            scaled = scale_profile_for_seniority(self.profile, level, self.settings.resume.seniority_years_factor)
            self.assertEqual(scaled['years_experience_total'], self.profile['years_experience_total'])
            self.assertEqual(scaled['skill_years'], self.profile['skill_years'])
        self.assertEqual(self.profile['skill_years_default'], 0)

    def test_old_resumes_are_not_reused_after_profile_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resume.json'
            path.write_text(json.dumps({'_meta': {'profile_hash': profile_fingerprint(self.profile)}}))
            self.assertIsNotNone(_load(path, self.profile))
            changed = copy.deepcopy(self.profile)
            changed['summary'] = 'Resumo atualizado'
            self.assertIsNone(_load(path, changed))
            path.write_text('{}')
            self.assertIsNone(_load(path, self.profile))


if __name__ == '__main__':
    unittest.main()
