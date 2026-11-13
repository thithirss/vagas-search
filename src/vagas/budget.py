"""Orçamento de IA: preço por modelo, gasto acumulado e freio quando estoura o teto.

O usuário fixou um teto mensal em reais. Aqui ficam o preço de cada modelo, a conversão
para BRL e a decisão de deixar (ou não) uma chamada acontecer. A regra é degradar, não
quebrar: sem orçamento, o que é determinístico continua rodando e só o que depende de IA
é adiado, com motivo claro na fila.
"""
from __future__ import annotations

import time
from datetime import date

# US$ por 1 milhão de tokens: (entrada, entrada em cache, saída). Fonte: página oficial de preços da OpenAI
# (developers.openai.com/api/docs/pricing, conferida em 13/11/2026, contexto até 272K). Nos modelos que "pensam",
# o raciocínio já vem somado em completion_tokens e é cobrado como saída.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
}
DEFAULT_PRICE = (10.00, 1.00, 50.00)   # desconhecido: assume o mais caro da tabela, para o freio errar a favor


def price(model: str) -> tuple[float, float, float]:
    m = (model or "").strip()
    if m in PRICES:
        return PRICES[m]
    # versão com data ("gpt-6.1-sol-2026-09-01") usa o preço do nome-base
    base = next((k for k in sorted(PRICES, key=len, reverse=True) if m.startswith(k + "-")), None)
    return PRICES[base] if base else DEFAULT_PRICE


def call_cost_usd(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    pin, pcached, pout = price(model)
    cached = min(int(cached_tokens or 0), int(prompt_tokens or 0))
    fresh = int(prompt_tokens or 0) - cached
    return fresh / 1e6 * pin + cached / 1e6 * pcached + (completion_tokens or 0) / 1e6 * pout


def month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


class Budget:
    """Lê o gasto do mês do SQLite e diz se ainda cabe uma chamada."""

    def __init__(self, db, monthly_brl: float, usd_brl: float, reserve_for_answers: float = 0.25,
                 since: str = "", overrides_brl: dict | None = None):
        self.db = db
        self.default_brl = float(monthly_brl or 0)
        self.overrides = {str(k): float(v) for k, v in (overrides_brl or {}).items()}
        # teto do mês corrente: exceção pontual (ex.: setembro com os US$ 5 de crédito novo) ou o padrão
        self.monthly_brl = self.overrides.get(month_key(), self.default_brl)
        # conta o gasto só a partir desta data (dentro do mês): crédito novo começa do zero
        self.since = (since or "").strip()
        self.usd_brl = float(usd_brl or 5.5)
        # fatia guardada para respostas de formulário: sem elas a candidatura trava no meio,
        # enquanto um currículo a menos só significa reaproveitar outro já pronto.
        self.reserve = float(reserve_for_answers)
        self._cache: dict[str, tuple[float, float]] = {}

    # ---------- leitura ----------
    def spent_usd(self, month: str | None = None) -> float:
        """Gasto do mês. Cacheado por alguns segundos: o freio roda antes de CADA chamada."""
        month = month or month_key()
        now = time.time()
        hit = self._cache.get(month)
        if hit and now - hit[0] < 20:
            return hit[1]
        start = f"{month}-01"
        if self.since and self.since[:7] == month and self.since > start:
            start = self.since
        cols = {r[1] for r in self.db.conn.execute("PRAGMA table_info(llm_usage)")}
        cached = "COALESCE(cached_tokens, 0)" if "cached_tokens" in cols else "0"
        rows = self.db.conn.execute(
            f"SELECT model, prompt_tokens, completion_tokens, {cached} FROM llm_usage "
            "WHERE strftime('%Y-%m', at) = ? AND at >= ?",
            (month, start),
        ).fetchall()
        total = sum(call_cost_usd(r[0], r[1], r[2], r[3]) for r in rows)
        self._cache[month] = (now, total)
        return total

    def spent_brl(self, month: str | None = None) -> float:
        return self.spent_usd(month) * self.usd_brl

    def remaining_brl(self) -> float:
        return max(0.0, self.monthly_brl - self.spent_brl())

    def status(self) -> dict:
        spent = self.spent_brl()
        return {
            "month": month_key(),
            "since": self.since if self.since[:7] == month_key() else "",
            "spent_brl": round(spent, 2),
            "budget_brl": round(self.monthly_brl, 2),
            "remaining_brl": round(max(0.0, self.monthly_brl - spent), 2),
            "used_pct": round(100 * spent / self.monthly_brl, 1) if self.monthly_brl else 0.0,
        }

    # ---------- decisão ----------
    def allows(self, purpose: str) -> bool:
        """Cabe gastar IA com este propósito agora?

        Sem teto configurado, tudo passa. Perto do fim do mês de orçamento, só passa o que é
        essencial para concluir uma candidatura já começada (respostas de formulário).
        """
        if self.monthly_brl <= 0:
            return True
        left = self.remaining_brl()
        if left <= 0:
            return False
        if left <= self.monthly_brl * self.reserve:
            return purpose in ("answer_questions",)
        return True

    def why_blocked(self, purpose: str) -> str:
        s = self.status()
        return (f"orçamento de IA do mês esgotado (R$ {s['spent_brl']:.2f} de R$ {s['budget_brl']:.2f})"
                if s["remaining_brl"] <= 0 else
                f"orçamento de IA quase no fim (R$ {s['remaining_brl']:.2f} restantes): '{purpose}' fica para o próximo mês")


class BudgetExceeded(Exception):
    """IA bloqueada pelo teto mensal. O chamador deve seguir pelo caminho determinístico."""
