# Rodar no Windows

Verificado em 29/10/2026: Python 3.12.10 (instalado via winget, escopo do usuário), dependências
em `.venv`, Google Chrome do sistema (`channel: chrome`), geração de PDF, painel HTTP,
iniciar/parar tarefas pelo painel e os 48 testes (`python -m unittest discover -s tests`).
Nenhuma busca ou candidatura foi feita durante a verificação.

## Abrir o ambiente (PowerShell)

```powershell
cd C:\caminho\onde\voce\clonou\vagas
.\.venv\Scripts\Activate.ps1
vagas doctor
```

Se o PowerShell bloquear o `Activate.ps1`, use direto `.\.venv\Scripts\vagas.exe doctor`.

## Primeiro passo obrigatório: logar de novo

O perfil do navegador (`data/profile`) veio do Linux. O Chrome do Windows criptografa cookies
de outro jeito, então as sessões do LinkedIn/Indeed/Gupy não valem aqui:

```powershell
vagas login
```

O Indeed recusa o navegador automatizado logo no e-mail ("Algo deu errado. Atualize a página").
Para ele, use o Chrome comum no mesmo perfil do bot e feche a janela quando terminar:

```powershell
vagas login --normal --site indeed
```

## Uso diário

- **Painel de Vagas** (Menu Iniciar) ou `abrir_painel.bat`: só abre o painel (http://127.0.0.1:8765),
  sem iniciar o bot. Pelo terminal: `.\.venv\Scripts\vagas.exe dashboard`.
- **Iniciar Bot de Vagas** (Menu Iniciar) ou `iniciar_bot.bat`: abre o painel e já inicia o bot.
- No painel: "Buscar vagas", "Ensaiar" (preenche e para antes de enviar), "Enviar" e "Rodar o dia"
  (pedem confirmação). **Parar** termina a vaga atual, fecha o Chrome e sai (até ~1 min);
  "Forçar agora" encerra na hora.
- Pendências de login/conta: "Abrir no navegador do bot" abre um Chrome comum com o perfil do bot;
  faça o login, feche a janela e clique em "Já resolvi". Só com o bot parado.
- Comandos do terminal continuam os mesmos do `INSTRUCOES_LOCAL.md` (`vagas search`, `vagas apply`,
  `vagas dashboard`, `vagas questions`, `vagas custos`...).

## Painel no celular

Toda vez que o painel inicia, ele abre um túnel da Cloudflare (`tools/cloudflared.exe`, programa oficial e
assinado) e manda para o seu Telegram um link `https://…trycloudflare.com/?k=…`. O link muda a cada início;
`/painel` no Telegram reenvia o atual. Sem a chave do link o painel responde "acesso negado". Não compartilhe
o link. Para desligar, ponha `remote_access: false` no `config/settings.yaml`. Para trocar a chave (se o link
vazar), apague a chave com:
`.\.venv\Scripts\python.exe -c "from vagas.config import load_settings; from vagas.db import DB; DB(load_settings().db_path).conn.execute('delete from kv where key=''remote_key'''); "`
e reinicie o painel.

## Retornos por e-mail

O bot lê o Gmail a cada 30 minutos (enquanto está rodando, inclusive de madrugada) e registra as respostas das empresas: positivo
(entrevista, teste, próxima etapa), negativo e em processo. Elas aparecem em **Retornos** no painel, no
comando /retornos do Telegram, e cada retorno positivo novo chega na hora no Telegram.

Para ligar, crie uma **senha de app** do Google (a leitura é só de leitura: nada é marcado, respondido ou apagado):

1. Abra https://myaccount.google.com/apppasswords (exige a verificação em duas etapas ligada na conta).
2. Dê um nome (por exemplo "bot de vagas") e copie a senha de 16 letras.
3. No arquivo `.env` do projeto, acrescente a linha `GMAIL_APP_PASSWORD=` seguida da senha (pode colar com os espaços).
4. Teste: `.\.venv\Scripts\vagas.exe retornos --dias 10`, ou o botão "Verificar e-mails agora" na tela Retornos.

Para revogar o acesso, apague a senha de app na mesma página do Google.

## Telegram

Com o painel aberto, mande comandos para o seu bot do Telegram (crie um com o @BotFather e ponha o token no .env) (só o seu chat é atendido):
/status, /hoje, /enviadas, /fila, /pendencias, /log, /iniciar (pede confirmação), /parar, /buscar,
/avisos (liga/desliga o aviso a cada candidatura) e /ajuda.

## O que mudou no código para o Windows

- `dashboard.py`: checar se a tarefa está viva usa a API do Windows (lá `os.kill(pid, 0)` **mata** o
  processo); "Parar" usa `taskkill /T /F`, que derruba o Python e o Chrome filho juntos.
- `scripts/iniciar_bot.py`: trava com `msvcrt` em vez de `fcntl`; processos destacados com
  `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`.
- `vagas/__init__.py`: stdout/stderr em UTF-8 (console e logs em cp1252 quebravam nos emojis).
- `vagas doctor`: procura o Chrome nos caminhos do Windows e não exige `DISPLAY`.
- `browser.py`: os flags de WebGL por software (`--use-angle=swiftshader` etc., remendo do WSL) só
  entram no Linux. No Windows eles deixavam a janela do Chrome toda escura.
- Atalho "Iniciar Bot de Vagas" no Menu Iniciar, apontando para `iniciar_bot.bat`.

As pastas `.venv-linux` (Linux) e `.venv-antigo-windows` (de outra máquina, apontava para
`C:\Users\Thi\...`) não são usadas e podem ser apagadas.
