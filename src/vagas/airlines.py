"""Trilha especial: vagas em companhia aérea (pedido do usuário em 2026-10-29: quer o benefício de viagens).

Vaga de companhia aérea ganha exceções que as outras não têm: suporte técnico/infra de TI também vale (ele
tem experiência como Analista de Suporte de TI), a aderência à stack de dev não barra, e ela fura a fila. As
demais regras continuam: remoto ou presencial/híbrido na região (Confins e Pampulha estão em
`discovery.local_cities`), sem vaga que exige inglês, sem sênior.
"""
from __future__ import annotations

import re
import unicodedata

# nome da empresa como aparece no LinkedIn/Indeed/Gupy. "Azul" sozinho pegaria Azul Seguros; por isso o sobrenome.
AIRLINE_RE = re.compile(
    r"\b(latam (airlines|brasil|cargo)|tam linhas|gol (linhas|intelig)|voegol|azul (linhas|conecta|viagens|cargo)|voeazul|voepass|passaredo|map linhas|"
    r"sideral|total linhas|abra group|tap (air|portugal)|american airlines|united airlines|delta air|air france|klm|"
    r"copa airlines|avianca|aerol[ií]neas|iberia|lufthansa|emirates|qatar airways|turkish airlines|air canada|"
    r"british airways|airlines|airways|linhas a[eé]reas|companhia a[eé]rea)\b", re.I)

# papéis de TI fora de desenvolvimento que valem nas aéreas
IT_SUPPORT_RE = re.compile(
    r"suporte|service ?desk|help ?desk|infraestrutura|infra\b|\bti\b|tecnologia da informa|redes|\bnoc\b|"
    r"field (service|support)|t[eé]cnico (de |em )?(inform[aá]tica|ti|sistemas|suporte)|analista de (sistemas|ti|suporte|infra)|"
    r"\bn[123]\b|sustenta[cç][aã]o|cloud|devops|sre|seguran[cç]a da informa", re.I)


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def is_airline(company: str | None, cfg=None) -> bool:
    if cfg is not None and not getattr(cfg, "enabled", True):
        return False
    c = company or ""
    extra = [x for x in (getattr(cfg, "extra_companies", None) or []) if x]
    return bool(AIRLINE_RE.search(c)) or any(_fold(x) in _fold(c) for x in extra)


# "Analista de Infraestrutura Aeroportuária (Lado Ar)" é engenharia civil/operação, não TI
NOT_IT_RE = re.compile(r"aeroportu|lado (ar|terra)|pista|manuten[cç][aã]o (de )?aeronave|oficina|ferramentaria|"
                       r"comiss[aá]ri|piloto|despachante|atendimento ao cliente|cargas?\b", re.I)


def is_it_support(title: str | None, description: str | None = "") -> bool:
    t = title or ""
    if NOT_IT_RE.search(t):
        return False
    return bool(IT_SUPPORT_RE.search(t)) or bool(IT_SUPPORT_RE.search((description or "")[:600]))
