from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

# ============================================================
# REGRAS FIXAS: teto de idade da vaga e só remoto — exceto presencial/híbrido nas cidades de
# `discovery.local_cities` (pedido do usuário em 2026-10-29: aceita BH e região; teto de idade 24h -> 72h
# para ter mais volume). Valem para toda busca e toda vaga, independentemente do settings.yaml.
# ============================================================
HARD_MAX_AGE_HOURS = 72
HARD_REMOTE_ONLY = True
AIRLINE_MAX_AGE_HOURS = 720   # teto da trilha de companhia aérea (vagas raras): 30 dias

ROOT = Path(os.environ.get("VAGAS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")


def resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


class Search(BaseModel):
    source: str
    query: str
    location: str = "Brasil"
    remote_only: bool = True
    posted_within: str = "24h"
    local: bool = False          # busca na região do usuário (discovery.local_location): presencial/híbrido valem
    airline: bool = False        # trilha de companhia aérea: janela própria (airlines.max_age_hours)

    @model_validator(mode="after")
    def _hard_rules(self) -> "Search":
        self.remote_only = True if (HARD_REMOTE_ONLY and not self.local) else self.remote_only
        cap = AIRLINE_MAX_AGE_HOURS if self.airline else HARD_MAX_AGE_HOURS
        if self.seconds > cap * 3600:
            self.posted_within = f"{cap}h"
        return self

    @property
    def seconds(self) -> int:
        s = self.posted_within.strip().lower()
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("d"):
            return int(s[:-1]) * 86400
        return 86400


class Discovery(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["linkedin", "indeed"])
    location: str = "Brasil"
    linkedin_geo_id: str = "106057199"          # Brasil
    linkedin_mode: str = "guest"                # guest = endpoint público sem conta | session = busca logada
    base_queries: list[str] = Field(default_factory=lambda: ["desenvolvedor", "programador", "engenheiro de software", "tech lead"])
    stack_queries: list[str] = Field(default_factory=list)
    stack_template: str = "desenvolvedor {stack}"
    stack_queries_sources: list[str] = Field(default_factory=lambda: ["linkedin"])
    seniority_terms: list[str] = Field(default_factory=list)
    max_pages_per_search: int = 3
    max_age_hours: int = HARD_MAX_AGE_HOURS
    # presencial/híbrido aceitos só nestas cidades; as buscas locais usam local_location sem filtro de remoto
    local_location: str = ""
    local_cities: list[str] = Field(default_factory=list)
    local_queries: list[str] = Field(default_factory=list)   # vazio = base_queries
    local_sources: list[str] = Field(default_factory=list)   # vazio = todas; o Indeed local trazia eletricista/limpeza

    @model_validator(mode="after")
    def _clamp(self) -> "Discovery":
        self.max_age_hours = min(self.max_age_hours, HARD_MAX_AGE_HOURS)
        return self


class FitCultural(BaseModel):
    """Teste de Fit Cultural da Gupy respondido pelo bot (vagas/fitcultural.py), sem IA.

    Cada frase do teste mede uma dimensão de cultura; o bot ordena as frases pela prioridade abaixo. Quem já gravou um
    gabarito próprio (data/fitcultural/gupy.json) usa o gabarito; quem acabou de clonar usa esta lista (o `vagas setup`
    pergunta)."""
    enabled: bool = True
    # cla = pessoas/equipe · adhocracia = inovação/flexibilidade · mercado = resultado/metas · hierarquia = estrutura/estabilidade
    priority: list[str] = Field(default_factory=lambda: ["cla", "adhocracia", "mercado", "hierarquia"])
    # Mapeamento comportamental (Big Five, 3 frases por questão): disciplina = organização/entrega · abertura = curiosidade,
    # tecnologia · estabilidade = calma · amabilidade = cooperação · extroversao = sociabilidade · impulso = decidir no impulso
    behavioral_enabled: bool = True
    behavioral_priority: list[str] = Field(default_factory=lambda: ["disciplina", "abertura", "estabilidade", "amabilidade",
                                                                    "extroversao", "impulso"])


class Airlines(BaseModel):
    """Trilha de companhia aérea (vagas/airlines.py): buscas próprias + exceções de filtro só para essas vagas."""
    enabled: bool = False
    search_companies: list[str] = Field(default_factory=list)   # nomes usados nas buscas ("LATAM", "GOL Linhas Aéreas"...)
    search_roles: list[str] = Field(default_factory=list)       # papéis buscados em cada uma ("desenvolvedor", "suporte técnico"...)
    search_sources: list[str] = Field(default_factory=lambda: ["linkedin"])   # a busca da Gupy é só por título, não acha por empresa
    extra_companies: list[str] = Field(default_factory=list)    # nomes extras reconhecidos como aérea
    allow_it_support: bool = True                               # suporte/infra de TI vale nas aéreas
    title_blacklist_exempt: list[str] = Field(default_factory=lambda: ["suporte", "help desk"])
    min_fit_score: int = 0                                      # aderência à stack de dev não barra vaga de aérea
    max_age_hours: int = 720                                    # vagas de TI em aérea são raras: janela de 30 dias


class Filters(BaseModel):
    allowed_seniorities: list[Literal["junior", "mid", "senior", "lead", "unspecified"]] = Field(default_factory=list)
    preferred_seniorities: list[str] = Field(default_factory=list)
    unspecified_min_fit_score: int = 80
    seniority_filter_since: date | None = None  # data de descoberta; vagas anteriores são preservadas
    apply_roles: list[str] = Field(default_factory=list)
    exclude_roles: list[str] = Field(default_factory=list)
    title_blacklist: list[str] = Field(default_factory=list)
    company_blacklist: list[str] = Field(default_factory=list)
    host_blacklist: list[str] = Field(default_factory=list)   # ATS/portais que não queremos (ex.: talynx.com.br)
    job_languages: list[str] = Field(default_factory=lambda: ["pt-BR"])   # idiomas aceitos na descrição da vaga
    reject_if: dict[str, bool] = Field(default_factory=dict)


class Limits(BaseModel):
    max_applications_per_day: int = 80
    max_per_source_per_day: dict[str, int] = Field(default_factory=dict)
    # Ritmo entre ENVIOS. O cuidado é com o LinkedIn/Indeed perceberem padrão de robô, então:
    #  - Easy Apply e Indeed Apply (a candidatura acontece dentro da plataforma): ritmo humano;
    #  - externa (Gupy, InHire, site da empresa): cada envio é num site diferente, espera menor;
    #  - tentativa que não enviou nada (login, conta, anúncio expirado): quase sem espera.
    delay_between_applications: tuple[int, int] = (45, 150)
    delay_between_applications_external: tuple[int, int] = (15, 45)
    delay_after_failure: tuple[int, int] = (3, 10)
    long_break_every: int = 10          # conta só ENVIOS pelo Easy Apply/Indeed Apply
    long_break_minutes: tuple[int, int] = (6, 15)
    active_hours: tuple[str, str] = ("08:00", "23:30")
    discovery_interval_minutes: int = 120
    inbox_interval_minutes: int = 30   # leitura do Gmail (retornos) enquanto o bot roda, inclusive fora da janela ativa
    # Indeed barrou o robô (Cloudflare/login): pausa as vagas do Indeed por N horas, sem gastar tentativa
    indeed_block_hours: float = 3.0
    max_form_steps: int = 12
    human_checkpoint_timeout_minutes: int = 15
    checkpoint_auto_seconds: int = 45     # espera curta quando não há ninguém no teclado (depois pula a vaga)


class BrowserCfg(BaseModel):
    mode: str = "persistent"  # persistent | cdp
    user_data_dir: str = "data/profile"
    channel: str = ""
    executable_path: str = ""   # binário do Chrome (ex.: ~/.local/chrome/opt/google/chrome/chrome); vazio = channel/Chromium
    cdp_url: str = "http://localhost:9222"
    locale: str = "pt-BR"
    timezone: str = "America/Sao_Paulo"
    headless: bool = False
    slow_mo_ms: int = 0


class LLMCfg(BaseModel):
    cheap_model: str = "gpt-4.1-mini"
    smart_model: str = "gpt-4.1"
    # modelos que "pensam antes de responder" (GPT-5/GPT-6): esforço de raciocínio pedido ("low" = barato e rápido;
    # "" = padrão do modelo). Quem não aceita o parâmetro é lembrado e chamado sem ele.
    reasoning_effort: str = "low"
    # modelos oferecidos na preparação de entrevista (tela Entrevistas), do mais barato ao mais caro
    prep_models: list[str] = Field(default_factory=lambda: ["gpt-6-luna", "gpt-4.1-mini", "gpt-6.1-terra", "gpt-4.1", "gpt-6.1-sol", "gpt-6-astra"])
    min_answer_confidence: float = 0.6
    always_ask_topics: list[str] = Field(default_factory=list)
    # Teto de gasto com IA. 0 = sem teto. O freio degrada em vez de quebrar: sem orçamento,
    # só roda o que é determinístico (classificador de regras, currículo reaproveitado, banco de respostas).
    monthly_budget_brl: float = 50.0
    usd_brl: float = 5.5
    # Conta o gasto só a partir desta data (AAAA-MM-DD) dentro do mês — para crédito novo começar do zero.
    budget_since: str = ""
    # Teto específico de um mês ("AAAA-MM": R$). Mês sem entrada usa monthly_budget_brl.
    budget_overrides_brl: dict[str, float] = Field(default_factory=dict)
    # Confiança mínima do classificador determinístico para dispensar a IA.
    rules_min_confidence: float = 0.75
    # Campo OPCIONAL sem resposta por regra/banco fica em branco em vez de ir para a IA.
    skip_optional_without_rule: bool = True
    # Plano B do formulário (vagas/rescue.py): quando as regras não acham o botão de candidatar/avançar, o modelo
    # barato escolhe entre os elementos clicáveis da tela. O que funcionou fica gravado por site (sem IA na próxima).
    rescue_enabled: bool = True
    rescue_max_per_job: int = 3


class ResumeCfg(BaseModel):
    language: str = "auto"
    adapt_seniority: bool = True
    # skill_years e years_experience_total do perfil são os REAIS (sênior); por nível usamos uma fração
    seniority_years_factor: dict[str, float] = Field(default_factory=lambda: {"junior": 0.25, "mid": 0.5, "senior": 1.0, "lead": 1.0, "unspecified": 1.0})
    max_experiences_junior: int = 4
    max_experiences_mid: int = 99
    max_experiences_senior: int = 99
    output_dir: str = "data/resumes"
    template: str = "modern"           # modern | classic (templates em src/vagas/resume/templates)
    accent_color: str = "#1f3a93"      # cor dos títulos, cargo, chips
    photo_mm: int = 38                 # tamanho da foto no cabeçalho (0 = sem foto)
    certifications_when_none: str = "hide"
    upload_filename: str = "Curriculo - {first_name} {last_name}.pdf"   # nome neutro do arquivo enviado (o interno fica em data/resumes)   # hide | generic: o que fazer quando nenhum curso do pool casa com a vaga


class ApplyCfg(BaseModel):
    dry_run: bool = False                 # preenche tudo mas NÃO clica em enviar
    pause_before_submit: bool = False     # no modo interativo, pede confirmação antes de enviar
    min_fit_score: int = 0                # 0 = aplica para toda vaga de dev; >0 filtra por aderência
    apply_once_per_company_days: int = 0  # 0 = desligado; N = no máximo 1 candidatura por empresa a cada N dias
    max_attempts: int = 2                 # tentativas por vaga antes de marcar como failed definitivo


class CoursesCfg(BaseModel):
    auto_suggest: bool = True        # ao classificar uma vaga, sugere >= min_udemy cursos da Udemy para cada stack nova (cache em config/courses.yaml)
    min_udemy: int = 3
    auto_enroll: bool = False        # true = cursos sugeridos da stack da vaga aparecem no PDF como "em andamento" (você se compromete a se matricular)
    max_in_progress_on_resume: int = 3


class ATSCfg(BaseModel):
    skip_hosts: list[str] = Field(default_factory=list)
    known_hosts: dict[str, str] = Field(default_factory=dict)


class Settings(BaseModel):
    discovery: Discovery = Field(default_factory=Discovery)
    searches: list[Search] = Field(default_factory=list)      # buscas extras, além da matriz
    max_pages_per_search: int = 3
    filters: Filters = Field(default_factory=Filters)
    airlines: Airlines = Field(default_factory=Airlines)
    fitcultural: FitCultural = Field(default_factory=FitCultural)
    remote_access: bool = True     # link do painel para o celular (túnel Cloudflare + chave, vagas/remote.py)
    limits: Limits = Field(default_factory=Limits)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    resume: ResumeCfg = Field(default_factory=ResumeCfg)
    apply: ApplyCfg = Field(default_factory=ApplyCfg)
    courses: CoursesCfg = Field(default_factory=CoursesCfg)
    ats: ATSCfg = Field(default_factory=ATSCfg)

    def all_searches(self) -> list[Search]:
        """Matriz base_queries x sources + stack_queries (nas fontes configuradas) + buscas locais + extras.
        Remotas no Brasil todo; locais (discovery.local_location) sem filtro de remoto. Cada nível (júnior, pleno…)
        sai inteiro antes do próximo, locais incluídas."""
        d = self.discovery
        out: list[Search] = []
        seen: set[tuple[str, str, str]] = set()

        def add(src: str, q: str, local: bool = False, airline: bool = False) -> None:
            loc = d.local_location if local else d.location
            key = (src, q.strip().lower(), loc)
            if key in seen or not q.strip():
                return
            seen.add(key)
            age = min(self.airlines.max_age_hours, AIRLINE_MAX_AGE_HOURS) if airline else d.max_age_hours
            out.append(Search(source=src, query=q.strip(), location=loc, remote_only=not local, local=local, airline=airline,
                              posted_within=f"{age}h"))

        for level in d.seniority_terms or [""]:
            for src in d.sources:
                for q in d.base_queries:
                    add(src, f"{q} {level}".strip())
                if src in d.stack_queries_sources:
                    for st in d.stack_queries:
                        add(src, f"{d.stack_template.format(stack=st)} {level}".strip())
                if d.local_location and (not d.local_sources or src in d.local_sources):   # região do usuário: presencial/híbrido aceitos em discovery.local_cities
                    for q in (d.local_queries or d.base_queries):
                        add(src, f"{q} {level}".strip(), local=True)
        for extra in self.searches:
            add(extra.source, extra.query)
        # trilha de companhia aérea: "<papel> <companhia>", remoto no Brasil e presencial/híbrido na região
        a = self.airlines
        if a.enabled:
            for comp in a.search_companies:
                for role in a.search_roles:
                    for src in a.search_sources:
                        add(src, f"{role} {comp}", airline=True)
                        if d.local_location:
                            add(src, f"{role} {comp}", local=True, airline=True)
        return out

    @property
    def profile_path(self) -> Path:
        return CONFIG_DIR / "profile.yaml"

    @property
    def answers_path(self) -> Path:
        return CONFIG_DIR / "answers.yaml"

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "vagas.db"


def load_settings(path: Path | None = None) -> Settings:
    path = path or CONFIG_DIR / "settings.yaml"
    example = CONFIG_DIR / "settings.example.yaml"
    if not path.exists() and path.parent == CONFIG_DIR and example.exists():
        # clone novo: o settings.yaml é pessoal (fora do git) e nasce do exemplo
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    s = Settings.model_validate(raw)
    # overrides por env
    if os.environ.get("VAGAS_LLM_CHEAP_MODEL"):
        s.llm.cheap_model = os.environ["VAGAS_LLM_CHEAP_MODEL"]
    if os.environ.get("VAGAS_LLM_SMART_MODEL"):
        s.llm.smart_model = os.environ["VAGAS_LLM_SMART_MODEL"]
    if os.environ.get("VAGAS_BROWSER_MODE"):
        s.browser.mode = os.environ["VAGAS_BROWSER_MODE"]
    return s
