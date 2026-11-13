# vagas — auto-aplicador pessoal

Ferramenta de uso pessoal que **descobre vagas de desenvolvimento publicadas no dia** (LinkedIn, Indeed, Gupy), **gera um currículo adaptado por vaga** a partir do seu perfil mestre e **aplica automaticamente** (LinkedIn Easy Apply, Indeed Apply, Gupy, InHire, Greenhouse, Lever, Workable e qualquer outro ATS via preenchimento genérico guiado por LLM). Usa o **seu navegador logado** (perfil persistente), ritmo humano e tetos diários para não tomar bloqueio.

Não é produto. É pra rodar na sua máquina, com as suas contas.

## Começar do zero (Windows)

Nada pessoal vem no repositório: perfil, chaves, banco, currículos e sessões do navegador são criados
na sua máquina e ficam fora do git (`.gitignore`). Precisa de Python 3.12+ e do Google Chrome.

```powershell
git clone <url-deste-repositório> vagas
cd vagas
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\pip install patchright
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\patchright install chromium     # navegador usado para gerar o PDF do currículo

.\.venv\Scripts\vagas setup      # ⬅️ pergunta seus dados e escreve config/profile.yaml e .env
.\.venv\Scripts\vagas login      # entre no LinkedIn/Gupy no navegador do bot (Indeed: vagas login --normal --site indeed)
.\.venv\Scripts\vagas dashboard --serve   # painel em http://localhost:8765
```

- **Sua chave da OpenAI** vai no `.env` (o `vagas setup` pergunta). Sem ela a ferramenta roda pelo caminho sem IA.
  O gasto tem teto mensal em `config/settings.yaml` (`llm.monthly_budget_brl`).
- `config/settings.yaml` nasce de `config/settings.example.yaml` na primeira execução e é seu: buscas,
  blacklist de empresas, teto diário, janela de horário e a região aceita para vaga presencial/híbrida.
- Opcionais no `.env`: Telegram (avisos e comandos), Gmail com senha de app (retornos das empresas, só leitura),
  senha do LinkedIn (login na Gupy). Acesso ao painel pelo celular precisa de `tools/cloudflared.exe`
  (baixe do GitHub oficial da Cloudflare) ou desligue com `remote_access: false`.
- **Próximas etapas:** quando uma empresa responde, o bot lê o e-mail e cuida do resto: o **teste de Fit Cultural da
  Gupy é respondido sozinho, sem IA**, pela ordem de prioridade que o `vagas setup` pergunta (pessoas, inovação,
  resultado, estrutura; fica em `config/settings.yaml`, seção `fitcultural`). Inscrição não finalizada volta para a
  fila; entrevistas ganham um kit de preparação; respostas a recrutador só saem com o seu OK no Telegram ou no painel.
  Mapeamento comportamental da Gupy também é automático; testes de raciocínio, técnicos e entrevistas ficam com você (lista, prazo e lembrete). Não precisa do Claude.
- Detalhes do Windows (atalhos, parar/iniciar, armadilhas): `INSTRUCOES_WINDOWS.md`.

## Fit Cultural da Gupy (automático, sem IA)

Muitas vagas da Gupy pedem um **teste de Fit Cultural**: 10 questões, cada uma com 4 frases para ordenar de 1 (mais
importante) a 4. O bot responde sozinho, **sem IA e sem precisar do Claude**, assim que o e-mail "Etapa Fit Cultural
desbloqueada" chega.

**Como ele decide a ordem.** Cada frase do teste mede um de 4 valores de cultura, e o bot reconhece qual pelas palavras
da frase (testado nas 40 frases reais do teste: 40 certas):

| Nº | Valor | Exemplos de frase |
|---|---|---|
| 1 | Pessoas e equipe | "a equipe seja o aspecto mais importante", "união do grupo" |
| 2 | Inovação e flexibilidade | "flexível e adepta a mudanças", "tomar decisões de forma independente" |
| 3 | Resultado e metas | "orientada a lucros e resultados", "superar a concorrência" |
| 4 | Estrutura e estabilidade | "dia a dia bem estruturado", "ambiente de trabalho estável" |

Ele ordena as frases pela **sua prioridade** e alterna os dois primeiros lugares em algumas questões, para o resultado
não parecer "perfeito demais". Não existe resposta certa: a Gupy compara o seu resultado com o perfil que cada empresa
definiu. Empresas de tecnologia costumam valorizar mais pessoas e inovação.

**Como configurar:**
1. Rode `vagas setup`. No bloco de preferências ele pergunta a ordem: digite os 4 números do mais importante para o
   menos importante, por exemplo `1234` (pessoas, inovação, resultado, estrutura) ou `2134`. Enter mantém o padrão `1234`.
2. A ordem fica em `config/settings.yaml`:
   ```yaml
   fitcultural:
     enabled: true          # false = o bot não responde o teste (fica na lista para você fazer)
     priority: ["cla", "adhocracia", "mercado", "hierarquia"]   # pessoas, inovação, resultado, estrutura
   ```
3. Pronto. O `vagas run` responde os testes que chegarem e avisa no Telegram. Para rodar na hora: pare o bot e use
   `vagas etapas` (ou `vagas etapas --id N` para tentar de novo uma etapa específica, com o id da tela Retornos).

**Mapeamento comportamental (também automático, sem IA).** Mesmo formato, com 21 questões de 3 frases que descrevem
o seu jeito (método Big Five). O bot ordena pelos traços, nesta prioridade padrão para vagas de desenvolvimento:
organização e entrega (`disciplina`) > curiosidade e tecnologia (`abertura`) > calma sob pressão (`estabilidade`) >
cooperação (`amabilidade`) > sociabilidade (`extroversao`) > decidir no impulso (`impulso`). Ajuste em
`config/settings.yaml` (`fitcultural.behavioral_priority`, ou `behavioral_enabled: false` para fazer você mesmo).
Um gabarito gravado em `data/comportamental/gupy.json` tem prioridade.

**Gabarito próprio (opcional).** Se quiser respostas frase a frase, faça um teste você mesmo e grave as suas escolhas em
`data/fitcultural/gupy.json` ou `data/comportamental/gupy.json` (fora do git). Quando esse arquivo existe, ele tem prioridade sobre a configuração.

**Funciona para quem clonar este projeto?** Sim, os dois testes (Fit Cultural e Mapeamento comportamental) são
respondidos automaticamente **sem nenhuma configuração do teste**: sem gabarito gravado, o bot usa as prioridades padrão
de `config/settings.yaml` (que nasce de `config/settings.example.yaml`). Não precisa de IA, de chave da OpenAI nem do
Claude para isso. O que precisa estar configurado para o bot *chegar* até o teste:

| Requisito | Para quê | Onde |
|---|---|---|
| Gmail com **senha de app** (`GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD`) | ler o e-mail "Etapa … desbloqueada" e pegar o link da etapa | `.env` (o `vagas setup` pergunta) |
| **Senha do LinkedIn** (`LINKEDIN_PASSWORD`) | entrar na Gupy por "Entrar com LinkedIn" (a sessão da Gupy não fica salva) | `.env` |
| `vagas run` rodando (painel → Iniciar, ou `/iniciar` no Telegram) | ler o e-mail a cada 30 min e abrir os testes | — |

Opcional: mudar a ordem de prioridade (`vagas setup` pergunta a do Fit Cultural; a do comportamental fica em
`fitcultural.behavioral_priority`), desligar um dos testes (`enabled: false` / `behavioral_enabled: false`) ou gravar um
gabarito próprio em `data/`. Sem o Gmail configurado, as etapas não são detectadas e nada é respondido sozinho; dá para
rodar na hora com `vagas etapas` só depois que o e-mail tiver sido lido.

**Segurança.** O bot confere a ordem na tela antes de avançar. Se não reconhecer as frases ou não conseguir ordenar,
ele **para sem enviar**, avisa no Telegram com o link e guarda um retrato da tela em `data/learn/fitcultural/`.
Testes de raciocínio lógico, técnicos e entrevistas têm resposta certa ou medem a pessoa ao vivo e ficam com você: o bot só lista,
mostra o prazo e lembra.

## Entrevistas (treino dentro do painel)

A tela **Entrevistas** do painel prepara você para cada entrevista, em cima da vaga:

1. Na caixa "Qual entrevista vamos preparar?", cole a mensagem do recrutador ou escreva algo como
   `Acme, Desenvolvedor Python, hoje 14h`. O bot acha a vaga no banco (só quando a **empresa** aparece no texto),
   puxa salário, endereço, horário, modelo de trabalho e data, e mostra o que ele mesmo enviou naquela candidatura.
   Convites de entrevista que chegam no e-mail viram entrevista sozinhos.
2. Cada entrevista tem um treino montado **sem IA**:
   - **Flashcards**: "Sobre você" gerado do seu `config/profile.yaml` (pitch, liderança, problema difícil, pretensão do
     nível da vaga e uma resposta honesta para tecnologia que você ainda não domina), mais baralhos técnicos conforme a
     stack da vaga (Python, ponte Laravel → Python, JavaScript, HTTP/SQL/Git). Marque **Sabia** ou **Revisar de novo**.
   - **Simulado**: pergunta sorteada, cronômetro de 1 minuto, resposta-modelo e checklist do que não podia faltar.
   - **Quiz**: múltipla escolha com explicação.
   - **Roteiro**: pitch, pretensão, perguntas para fazer e checklist. O botão **Gerar preparação** usa IA (opcional,
     respeita o teto) e só fatos do perfil: você **escolhe o modelo** na lista (lista em `llm.prep_models` do
     `settings.yaml`), cada um com o custo estimado por preparação, um alerta colorido (verde econômico, amarelo,
     vermelho caro) e quanto sobra do teto do mês. Depois de gerar, aparece o custo real e o modelo usado.
   - **Vaga e anotações**: a descrição, as respostas enviadas no formulário e um campo para suas anotações.
3. Depois da entrevista, marque **Já fiz a entrevista** ou **Arquivar**. O progresso dos cartões fica no navegador.

Funciona para quem clonar: tudo vem do perfil de quem usa o bot; nada é inventado.

## Regras fixas (em código, não só em config)

- **Só vagas recentes** (até 72h, `config.py: HARD_MAX_AGE_HOURS`) e **só remoto**, exceto presencial/híbrido nas cidades de `discovery.local_cities` (vazio = só remoto). O resto é descartado antes de qualquer chamada de IA.
- **Só descrições em pt-BR** (`filters.job_languages`); descrições em inglês são descartadas por heurística sem IA. Vagas que exigem outro idioma além de inglês são puladas.
- **Descoberta no LinkedIn sem usar a conta**: endpoint público `jobs-guest` com `geoId` do Brasil + filtros 24h/remoto; a sessão logada só entra para candidatar. Indeed continua no navegador (Cloudflare). Gupy usa a API pública.
- **Cobertura por linguagem**: o LinkedIn personaliza "desenvolvedor" pelo seu perfil, então a descoberta roda uma matriz `base_queries` × fontes + `"desenvolvedor {stack}"` para cada item de `discovery.stack_queries` (php, rust, c#, java, ...). Hoje: 61 buscas no LinkedIn, 61 na Gupy, 10 no Indeed por ciclo.
- **Pretensão salarial**: se a vaga informa salário/faixa, a resposta segue a vaga; senão usa `facts.salary_expectation_<nível>_<regime>` (nível vem do classificador, regime CLT por padrão).
- **Anos de experiência por nível**: `skill_years` no perfil são os reais (sênior); vaga pleno usa metade, júnior um quarto (`resume.seniority_years_factor`), tanto no currículo quanto nas respostas de formulário.
- **Adaptação à stack da vaga**: `adaptation.min_stack_share` no perfil (0.5) obriga pelo menos 50% dos bullets e das competências a citar a stack da vaga. O gerador reescreve bullets trocando/acrescentando tecnologias permitidas nas experiências sem `restack: false`; empresa, cargo, datas, clientes e números não mudam. Se ficar abaixo da meta, tenta uma segunda vez. A fração alcançada fica em `_meta.stack_share` no JSON do currículo.
- **Design do currículo**: template `modern` (fonte Inter embutida, foto de 38 mm, cor de destaque `resume.accent_color`, chips de competências). `resume.template: classic` volta ao layout simples. Todas as experiências entram para pleno/sênior (2 páginas); júnior fica com 4.
- **Tecnologias declaradas**: `skills.also_experienced` lista linguagens/stacks com que o usuário declara experiência mas que não constam no LinkedIn; entram em Competências e no headline quando a vaga pede, com anos = `skill_years_default`. Os bullets de cada empresa continuam restritos ao que o perfil registra para aquela empresa (checagem de tecnologia por experiência).

## Como funciona

```
vagas search  ──► descobre (LinkedIn/Indeed/Gupy, últimas 24h)
                  ├─ abre cada vaga, lê a descrição
                  ├─ classifica com LLM: é vaga de dev? nível? stack? idioma? requisito eliminatório? fit 0-100
                  └─ fila (SQLite) ordenada por fit

vagas apply   ──► para cada vaga da fila, respeitando limites/ritmo:
                  ├─ gera currículo adaptado (LLM → HTML → PDF), validado contra o perfil (nada inventado)
                  ├─ escolhe o applier (Easy Apply / Indeed Apply / externo por host)
                  ├─ preenche etapa a etapa: regras → banco de respostas → LLM → você
                  └─ registra: applied / failed / pending_question (+ screenshot, dump para aprendizado)

vagas run     ──► loop do dia: descobre a cada N min e aplica dentro da janela ativa
```

Tudo que a ferramenta não souber responder vira **pergunta pendente**. Você responde uma vez (`vagas questions` ou modo `--interactive`), a resposta vai pro `config/answers.yaml` e nunca mais é perguntada.

## Setup

Precisa de Python 3.12+ e Linux/WSL.

```bash
cd ~/vagas
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install patchright            # fork stealth do Playwright (recomendado)
.venv/bin/patchright install chromium       # ou: .venv/bin/playwright install chromium

.venv/bin/vagas setup                       # ⬅️ COMECE AQUI
```

O `vagas setup` pergunta cada dado que a ferramenta usa e escreve `config/profile.yaml` e `.env`.
São 8 blocos (dados pessoais, documentos/endereço, diversidade, carreira, tecnologias, formação,
pretensão/preferências, chaves). Enter aceita o valor entre colchetes ou pula o campo — **nada é
inventado**: o que ficar em branco vira pendência quando um formulário pedir, em vez de sair um dado
errado. Cada bloco é salvo assim que termina, então dá para parar no meio (Ctrl+C) e retomar:

```bash
vagas setup --secao preferencias     # mexe só na pretensão salarial
vagas setup --secao carreira         # acrescenta uma experiência
vagas setup --secao chaves           # troca a chave da OpenAI
```

Blocos: `pessoal`, `documentos`, `diversidade`, `carreira`, `skills`, `formacao`, `preferencias`, `chaves`.

Se preferir preencher o YAML à mão, `vagas init` só copia os arquivos de exemplo. E dá para gerar um
rascunho da carreira a partir do PDF do LinkedIn (Perfil > Mais > Salvar como PDF) — isso usa IA e
**sobrescreve** o `profile.yaml`, então faça antes do resto:

```bash
vagas profile import ~/Downloads/Profile.pdf && vagas setup
```

Depois:

```bash
.venv/bin/vagas doctor        # confere chave, perfil, navegador e display
.venv/bin/vagas login         # loga no LinkedIn, Indeed e Gupy dentro do perfil do navegador (uma vez)
```

Dica: `alias vagas=~/vagas/.venv/bin/vagas` no `.zshrc` (ou `.bashrc`).

### O que revisar antes da primeira leva

- `config/profile.yaml` — principalmente os **bullets** de cada experiência: são a matéria-prima do
  currículo e o gerador só reescreve o que estiver lá.
- `config/settings.yaml` — `discovery.stack_queries` (as buscas por tecnologia), `filters.company_blacklist`,
  `limits` (ritmo e tetos diários) e `llm.monthly_budget_brl` (teto de gasto com IA, em reais).
- `config/courses.yaml` — catálogo de cursos por stack, já preenchido mas **tudo marcado como "a fazer"**.
  Marque os que você fez: `vagas courses done <trecho do nome>`. Só os marcados entram no currículo.

### Navegador: Chrome real (recomendado)

Chromium do Playwright reprova em Turnstile/Cloudflare (Workable, Greenhouse, Indeed). Duas opções:

1. **Chrome extraído sem root** (padrão em `settings.yaml`):
   ```bash
   curl -sSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
   mkdir -p ~/.local/chrome && dpkg -x /tmp/chrome.deb ~/.local/chrome
   ```
   `browser.executable_path: ~/.local/chrome/opt/google/chrome/chrome`
2. **Chrome do Windows via CDP** (fingerprint do seu Chrome de verdade):
   no Windows: `chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\vagas-profile`
   e em `settings.yaml`: `browser.mode: cdp`, `cdp_url: http://<ip-do-windows>:9222` (no WSL2 com mirrored networking, `localhost`).

Se nada disso existir, cai no Chromium do Patchright (funciona no LinkedIn; pode falhar em sites com Cloudflare).

## Uso diário

```bash
vagas search                       # descobre + classifica (últimas 24h)
vagas jobs --status queued         # o que entrou na fila
vagas apply --interactive --dry-run --limit 3   # calibração: preenche, NÃO envia, pergunta no terminal
vagas apply --interactive          # aplica de verdade; pergunta o que não souber
vagas apply                        # sem perguntar: o que não souber vira pendência
vagas questions                    # responde pendências -> volta pra fila
vagas run                          # modo contínuo (deixe num tmux)
vagas stats / vagas jobs / vagas answers
vagas resume linkedin:4123456789 --show   # ver o currículo gerado de uma vaga
vagas skip <id> / vagas requeue <id>
```

Primeiros dias: rode com `--interactive` para alimentar o banco de respostas (pretensão salarial, PJ/CLT, disponibilidade…). Depois `vagas run` sozinho.

## Configuração (`config/settings.yaml`)

- `discovery`: fontes, geoId, modo do LinkedIn (guest/session), `base_queries`, `stack_queries` e template. `searches` são extras (também forçadas a 24h + remoto).
- `filters`: `apply_roles` / `exclude_roles` guiam o classificador (aplicamos pra **qualquer linguagem**; o que importa é ser papel de dev). `title_blacklist` corta antes do LLM.
- `limits`: teto diário total e por fonte (LinkedIn Easy Apply tem teto não-publicado de ~50/24h), pausas entre candidaturas, pausa longa a cada N, janela ativa, intervalo de descoberta.
- `apply`: `dry_run`, `pause_before_submit`, `min_fit_score` (0 = todas), `apply_once_per_company_days`, `max_attempts`.
- `resume`: idioma (auto = idioma da vaga), adaptação por nível (júnior ⇒ menos experiências e bullets hands-on; **nunca** muda cargo/data; pode usar `title_alt` se você definir no perfil).
- `llm`: modelos (`cheap_model` para classificar/responder, `smart_model` para o currículo), confiança mínima, temas que sempre exigem sua resposta.
  O padrão é `gpt-6-luna` (triagem e formulários) e `gpt-6.1-terra` (currículo), com `reasoning_effort: low`: num teste
  com vagas reais saíram iguais ou melhores que o `gpt-4.1-mini`/`gpt-4.1` e mais baratos (triagem ~3x, currículo ~40%,
  e o terra quase não inventa tecnologia que a trava precisa cortar). Se a sua chave da OpenAI não tiver acesso a um
  modelo novo, o bot cai sozinho no equivalente antigo (`llm.FALLBACK_MODEL`) — não precisa mudar nada.
- `ats.known_hosts`: host → applier. `skip_hosts`: hosts que nem tentamos.

## Perfil mestre (`config/profile.yaml`)

Única fonte de verdade. O gerador de currículo só pode usar o que está lá:
- `skills.primary` (destaque), `secondary` (pode destacar), `familiar` (só em "Outros conhecimentos").
- `skill_years`: evita exagero em "anos de experiência com X".
- `experiences[].bullets`: escreva muitos, com números. `bullets_ic`: versões sem liderança (usadas em vagas júnior/pleno). `title_alt`: cargo alternativo permitido (ex.: sem "Sênior").
- `facts`: pretensão por nível, PJ/CLT, disponibilidade, modelos de trabalho, etc. Alimenta as respostas de formulário.
- `voice.notes`: como você escreve. Reduz a "cara de IA".

Depois de gerado, cada currículo passa por `validate_resume`: skills fora do perfil são removidas, empresas/datas/cargos são forçados a bater com o perfil.

## Aprendizado por ATS

Toda candidatura que falha ou fica pendente gera um dump em `data/learn/<ats>/<job>/stepN.{png,html,json}` (campos detectados, labels, opções). Use isso pra ajustar seletores/regras em `src/vagas/appliers/*.py` e `src/vagas/forms.py`. Sucesso gera `data/evidence/<job>.png`.

Fluxo típico pra "ensinar" um ATS novo: `vagas apply --job <id> --interactive --dry-run` → olhar `data/learn` → ajustar → repetir.

**Plano B com IA barata (aprende sozinho por site):** quando as regras não acham o botão de candidatar ou de avançar,
o `cheap_model` recebe a lista de botões visíveis da tela (sem dados seus) e escolhe qual clicar
(~R$ 0,002 por consulta, até `llm.rescue_max_per_job` por vaga, dentro do teto do mês). Nunca clica em login social,
alerta, compartilhar, cancelar ou "minhas candidaturas", e no `--dry-run` para antes de qualquer clique que registraria
a candidatura. Se o clique mudou a página, o texto do botão fica gravado para aquele site (`kv rescue:<site>:<objetivo>`)
e da próxima vez o mesmo site passa **sem IA**. Desliga com `llm.rescue_enabled: false`.

**Indeed com Cloudflare:** quando o Indeed pede "Confirme que é humano" ou login, a vaga volta para a fila sem gastar
tentativa e o Indeed fica em pausa por `limits.indeed_block_hours` (aviso no Telegram). Para destravar antes, entre no
Indeed pelo navegador do bot (painel → Abrir no navegador do bot) e confirme. O robô nunca resolve captcha.

## Anti-bloqueio (o que está implementado)

- Perfil persistente com sua sessão real; janela visível; flags de automação removidas; Patchright (fecha vazamentos de CDP como `Runtime.enable` e `navigator.webdriver`).
- Pausas com distribuição normal entre ações/candidaturas, digitação com velocidade variável, scroll de "leitura", pausa longa periódica, janela de horário.
- Tetos por dia e por fonte; detecção da mensagem de limite do LinkedIn (para a fonte até o dia seguinte).
- Checkpoints (login/captcha/Cloudflare): a ferramenta **para, apita e espera você** resolver na janela (timeout configurável); nunca tenta burlar captcha.
- Descoberta na Gupy via API pública (sem navegar).

## Estrutura

```
src/vagas/
  cli.py          comandos
  config.py       settings.yaml + .env
  db.py           SQLite (jobs, applications, pending_questions, events, llm_usage)
  browser.py      perfil persistente / CDP, checkpoints, wait_for_human
  humanize.py     pausas, digitação, scroll
  llm.py          OpenAI: classify_job, tailor_resume, answer_questions, extract_profile
  qa.py           banco de respostas (fuzzy) + regras + LLM + pergunta pra você
  forms.py        scanner JS de campos, preenchimento, botões, erros, sucesso
  pipeline.py     descoberta → preparação → currículo → candidatura, ritmo e limites
  sources/        linkedin.py, indeed.py, gupy.py (API)
  appliers/       flow.py (loop de etapas), linkedin.py, indeed.py, generic.py (+Gupy/InHire/Greenhouse/Lever/Workable), registry.py
  resume/         profile.py, tailor.py (LLM + validação), render.py (Jinja2 → PDF), importer.py (PDF do LinkedIn)
config/           settings.yaml, profile.yaml (seu), answers.yaml (aprendido)
data/             vagas.db, resumes/, profile/ (navegador), learn/, evidence/
```

## Dashboard

`vagas dashboard --serve` sobe um painel em http://localhost:8765 (abra no Chrome do Windows; o WSL encaminha localhost). Mostra candidaturas do dia e totais, vagas encontradas, fila, pendências, tokens de IA, funil de descarte, stacks mais pedidas, resultado por ATS, candidaturas enviadas com links e currículo, e falhas recentes com o motivo. Atualiza sozinho a cada 60 s. `vagas dashboard` sem `--serve` só gera `data/dashboard.html`.

## Credenciais opcionais no `.env`

- `GUPY_PASSWORD`: login automático nas páginas de empresa da Gupy (e-mail vem do perfil).
- `ATS_SIGNUP_PASSWORD`: senha usada para criar conta em ATS que exigem cadastro (Recrutei etc.). Vazio = a ferramenta não cria contas.

## Notificações (opcional)

`TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`: avisa perguntas pendentes, limites atingidos e resumo de cada rodada.

## Limitações conhecidas / próximos passos

- Seletores de LinkedIn/Indeed mudam; os do código seguem o DOM de 2025-26 com fallbacks por texto/role. Primeiras rodadas pedem calibração com `--dry-run`.
- Gupy ranqueia o **perfil do portal** (portal.gupy.io/curriculum), não o PDF: mantenha aquele perfil completo.
- Indeed costuma pedir captcha; resolva na janela quando apitar.
- Sem detecção de retorno (e-mails de entrevista) ainda; ver "funil" abaixo.

Ideias vindas de outros projetos (AIHawk, forks com Patchright, davamix, LuisMiguel/auto-apply-bot) que ainda não estão aqui: funil de retorno (triagem de e-mail/inbox), carta de apresentação quando pedida, notas de conexão com recrutador, estatísticas de skills mais pedidas, cap de custo de tokens.
