"""Áreas de vaga além do desenvolvimento e trilha regional — tudo opcional e escolhido na tela Preferências.

- `filters.adjacent_areas`: áreas de TI vizinhas que também valem (analista de sistemas, dados/BI, QA, SAP/ERP,
  automação/IA, suporte). O classificador continua decidindo "é dev?"; a vaga de área escolhida entra mesmo quando a
  resposta é "não" — sem exigir aderência à stack. Remoto/região, nível, idioma e blacklists continuam valendo.
- `region_track`: presencial/híbrido perto de UMA cidade (raio próprio), aceitando dev e, se quiser, suporte de TI;
  pode furar a fila e avisar no Telegram.

`Pipeline._track(job)` chama `track()` daqui; o dict devolvido diz quais exceções a vaga ganha.
"""
from __future__ import annotations

import re
import unicodedata

# chave -> (rótulo no painel, padrão no TÍTULO da vaga)
AREAS: dict[str, tuple[str, str]] = {
    "sistemas": ("Analista de sistemas, requisitos e negócios",
                 r"analista (de )?sistemas|analista de requisitos|requisitos de software|business analyst|"
                 r"analista de neg[oó]cios? (de |em )?(ti|sistemas|tecnologia|digital)|analista funcional|analista de integra[cç]"),
    "dados": ("Dados e BI",
              r"analista de (dados|bi\b|business intelligence|intelig[eê]ncia de dados|analytics)|data analyst|\bbi\b|power ?bi|"
              r"cientista de dados|data scientist|engenheir[oa] de dados|analytics|data visualization|visualiza[cç][aã]o de dados"),
    "qa": ("QA e testes",
           r"\bqa\b|quality assurance|qualidade de software|analista de testes?|testes? de software|automa[cç][aã]o de testes|"
           r"tester|\bsdet\b|test analyst|software test"),
    "erp": ("SAP, ERP e Salesforce",
            r"\bsap\b|\berp\b|totvs|protheus|salesforce|dynamics|netsuite|oracle (ebs|cloud|erp)|senior sistemas|sankhya"),
    "automacao_ia": ("Automação e IA (RPA, agentes, Claude Code, Codex…)",
                     r"automa[cç](ão|ao|[oõ]es)|\brpa\b|low.?code|no.?code|\bia\b|\bai\b|intelig[eê]ncia artificial|"
                     r"\bllm\b|gen ?ai|generativ|agentes?|ag[eê]ntic|prompt|machine learning|claude|codex|copilot|vibe coding|"
                     r"n8n|make\.com|zapier"),
    "suporte": ("Suporte técnico, infraestrutura e redes",
                r"suporte (t[eé]cnico|de ti|de sistemas|ao usu[aá]rio|em ti|de inform[aá]tica|n[123])|analista de suporte|"
                r"t[eé]cnic[oa] (de |em )?(suporte|inform[aá]tica|ti\b|redes|sistemas)|help ?desk|service ?desk|"
                r"analista de (ti|infraestrutura|redes|tecnologia)|assistente de (ti|suporte)|auxiliar de (ti|suporte)|"
                r"\bsuporte\b.*\b(ti|n[123]|sistemas|inform[aá]tica|tecnologia)\b|infraestrutura|\bnoc\b|cloud|sysadmin"),
}
AREA_RE = {k: re.compile(v[1], re.I) for k, v in AREAS.items()}

# contexto que NÃO é TI mesmo com as palavras acima
NOT_IT_RE = re.compile(r"automa[cç][aã]o (industrial|el[eé]trica|predial|residencial|comercial)|\bclp\b|\bplc\b|"
                       r"controle (e|&) automa[cç]|engenharia de controle|"
                       r"instrumenta[cç]|el[eé]tric|mec[aâ]nic|qualidade (industrial|de produto|assegurada de fornec)|"
                       r"laborat[oó]rio|marketing|vend|comercial|\bsdr\b|\bbdr\b|recrut|\brh\b|recursos humanos|administrativ|"
                       r"agente (de|comercial|administrativ|de viagens|de atendimento)|agentes? de (ia )?vendas|"
                       r"testes? (de materiais|f[ií]sic|cl[ií]nic|em campo)|enfermag|farmac|predial|"
                       r"manuten[cç][aã]o (de )?(aeronave|m[aá]quina|industrial)|log[ií]stic|telemarketing|"
                       r"seguran[cç]a do trabalho|cozinh|atendimento ao cliente", re.I)

DEV_RE = re.compile(r"desenvolv|developer|programad|engenheir[oa] de software|software engineer|full ?stack|front ?end|back ?end|"
                    r"analista (de )?sistemas|analista desenvolvedor|\bdev\b", re.I)

# títulos que a trilha de suporte NÃO deve pegar no "infraestrutura"/"cloud" genéricos fora de TI
_TITLE_EXEMPT_SUPPORT = ["suporte", "help desk"]


def _fold(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def area_of(title: str | None, areas: list[str]) -> str | None:
    """Primeira área escolhida que casa com o título (None se nenhuma, ou se o contexto não é TI)."""
    t = title or ""
    if not areas or NOT_IT_RE.search(t):
        return None
    return next((k for k in areas if k in AREA_RE and AREA_RE[k].search(t)), None)


def in_cities(job, cls: dict | None, cities: list[str]) -> bool:
    where = _fold(" ".join(str(x or "") for x in (job.location, (cls or {}).get("job_city"))))
    return any(c and re.search(rf"\b{re.escape(_fold(c).strip())}\b", where) for c in cities)


def region_role(title: str | None, rt) -> bool:
    t = title or ""
    if NOT_IT_RE.search(t):
        return False
    return bool((rt.include_dev and DEV_RE.search(t)) or (rt.include_support and AREA_RE["suporte"].search(t)))


def track(job, cls: dict | None, settings) -> dict | None:
    rt = getattr(settings, "region_track", None)
    if rt is not None and rt.enabled and rt.cities and in_cities(job, cls, rt.cities) and region_role(job.title, rt):
        return {"name": rt.name or "Trilha regional", "icon": "📍", "allow_non_dev": True, "priority": rt.priority,
                "notify": rt.notify, "title_exempt": _TITLE_EXEMPT_SUPPORT if rt.include_support else [], "min_fit": 0,
                "local": True}
    area = area_of(job.title, list(getattr(settings.filters, "adjacent_areas", None) or []))
    if area:
        return {"name": f"área: {AREAS[area][0]}", "allow_non_dev": True, "min_fit": 0,
                "title_exempt": _TITLE_EXEMPT_SUPPORT if area == "suporte" else []}
    return None
