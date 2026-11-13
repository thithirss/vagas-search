import unittest
from types import SimpleNamespace

from vagas import budget, llm as llm_mod
from vagas.config import LLMCfg, Settings


class FakeCompletions:
    def __init__(self, fail_models=(), reject_effort=False):
        self.calls, self.fail, self.reject_effort = [], set(fail_models), reject_effort

    def create(self, **kw):
        self.calls.append(dict(kw))
        if kw["model"] in self.fail:
            raise RuntimeError(f"Error code: 404 - The model `{kw['model']}` does not exist or you do not have access to it. model_not_found")
        if self.reject_effort and "reasoning_effort" in kw:
            raise RuntimeError("Unsupported parameter: 'reasoning_effort'")
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=usage)


def make_llm(**fake):
    l = llm_mod.LLM.__new__(llm_mod.LLM)
    l.cfg, l.db, l.budget = LLMCfg(), None, None
    l.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**fake)))
    return l


class ModelTests(unittest.TestCase):
    def setUp(self):
        llm_mod._NO_EFFORT.clear()

    def test_official_prices_and_dated_names(self):
        self.assertEqual(budget.price("gpt-6-luna"), (0.10, 0.01, 0.50))
        self.assertEqual(budget.price("gpt-6.1-sol-2026-09-01"), budget.price("gpt-6.1-sol"))
        self.assertEqual(budget.price("modelo-desconhecido"), budget.DEFAULT_PRICE)   # desconhecido: o mais caro (freio erra a favor)
        # 1M de entrada (metade em cache) + 1M de saída no luna
        self.assertAlmostEqual(budget.call_cost_usd("gpt-6-luna", 1_000_000, 1_000_000, 500_000), 0.05 + 0.005 + 0.50)

    def test_new_models_get_no_temperature_but_headroom_and_low_effort(self):
        l = make_llm()
        l.json(model="gpt-6-luna", system="s", user="u", purpose="t", temperature=0.2, max_tokens=120)
        kw = l.client.chat.completions.calls[-1]
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["max_completion_tokens"], 120 + llm_mod.REASONING_HEADROOM)
        self.assertEqual(kw["reasoning_effort"], "low")
        l.json(model="gpt-4.1-mini", system="s", user="u", purpose="t", temperature=0.2, max_tokens=120)
        kw = l.client.chat.completions.calls[-1]
        self.assertEqual((kw["temperature"], kw["max_completion_tokens"]), (0.2, 120))
        self.assertNotIn("reasoning_effort", kw)

    def test_model_without_access_falls_back_to_old_equivalent(self):
        l = make_llm(fail_models={"gpt-6.1-terra"})
        self.assertEqual(l.json(model="gpt-6.1-terra", system="s", user="u", purpose="t", temperature=0.5, max_tokens=500), {"ok": True})
        calls = l.client.chat.completions.calls
        self.assertEqual([c["model"] for c in calls], ["gpt-6.1-terra", "gpt-4.1"])
        self.assertEqual(calls[-1]["temperature"], 0.5)   # o antigo volta a receber temperatura
        self.assertNotIn("reasoning_effort", calls[-1])

    def test_model_that_rejects_effort_is_retried_without_it(self):
        l = make_llm(reject_effort=True)
        self.assertEqual(l.json(model="gpt-6-luna", system="s", user="u", purpose="t"), {"ok": True})
        self.assertNotIn("reasoning_effort", l.client.chat.completions.calls[-1])
        self.assertIn("gpt-6-luna", llm_mod._NO_EFFORT)

    def test_prep_model_options_have_estimated_cost(self):
        from vagas import interview
        opts = interview.prep_models(Settings())["models"]
        by = {o["model"]: o["brl"] for o in opts}
        self.assertLess(by["gpt-6-luna"], by["gpt-6.1-terra"])
        self.assertLess(by["gpt-6.1-terra"], by["gpt-6-astra"])
        self.assertTrue(any(o["default"] for o in opts))


if __name__ == "__main__":
    unittest.main()
