# Setup feito por um agente de IA

Roteiro para um agente (Claude Code, Codex, Cursor etc.) instalar o kindle-telegram-bot na máquina de uma
pessoa e deixá-lo funcionando para ela e para quem ela quiser. Siga em ordem; cada passo tem uma
verificação. Fale com a pessoa no idioma dela, em passos curtos: ela provavelmente nunca viu nada disso.

## Regras

- **Segredos** (token do bot, `api_hash`, senha de app): peça por um canal de segredo se o seu ambiente tiver
  um; senão, peça para a pessoa colar direto no arquivo `.env`. Nunca repita segredos no chat, em logs, em
  commits ou em comandos que fiquem no histórico. Guarde-os só no `.env` (modo 600).
- **Nada de commit do `.env`** nem de `data/` (já estão no `.gitignore`).
- **Uma instância por token.** Antes de subir, confira que não há outro processo do mesmo bot rodando.
- Se algo falhar, mostre à pessoa a linha de erro relevante (sem segredos) e o que fazer.

## 0. Conhecer a máquina

Descubra: sistema e arquitetura (`uname -m`; tudo funciona em amd64 e arm64), se há Docker/Podman, se a
máquina fica ligada o tempo todo, se há systemd, e espaço em disco (Calibre + imagens: ~1,5 GB).
Repare no `/tmp`: se for tmpfs pequeno, deixe `DATA_DIR` num disco de verdade (o bot já põe os temporários
do Calibre em `DATA_DIR/tmp`).

Escolha o caminho:
- **Docker/Podman disponível:** use o `docker-compose.yml` (passo 3A).
- **Sem containers:** Python 3.10+ e pacotes do sistema (passo 3B). O servidor local da Bot API ainda pode
  rodar em container; se não houver nenhum runtime de container, compile o
  [telegram-bot-api](https://github.com/tdlib/telegram-bot-api) ou aceite o limite de 20 MB
  (`BOT_API_URL=https://api.telegram.org`, pule `TELEGRAM_API_*` e o logout).

## 1. Coletar o que só a pessoa pode fazer

Peça uma coisa de cada vez, explicando onde clicar:

1. **Token do bot** — no Telegram, abrir [@BotFather](https://t.me/BotFather), `/newbot`, escolher nome e
   usuário (termina em `bot`). Ele responde com o token (`123456:AA...`).
   *Verifique:* `curl -s https://api.telegram.org/bot<TOKEN>/getMe` → `"ok":true`.
2. **`api_id` e `api_hash`** — em [my.telegram.org/apps](https://my.telegram.org/apps), logar com o número
   do Telegram, criar um app (qualquer nome; plataforma "Other"). `api_id` são só números; `api_hash` são
   32 caracteres hexadecimais.
3. **E-mail remetente** — o endereço que vai mandar os livros. No Gmail:
   - ligar a verificação em duas etapas (myaccount.google.com/signinoptions/twosv);
   - criar a senha de app em [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
     (16 letras).
   - Problemas conhecidos: "A configuração não está disponível para sua conta" = falta a verificação em
     duas etapas (ou conta de trabalho/escola, ou Proteção Avançada). "Não foi possível confirmar sua
     identidade" = conta nova ou aparelho desconhecido; tentar pelo celular onde a conta já está logada,
     esperar alguns dias, ou usar outra conta mais antiga.
   - Campos de colar às vezes cortam o fim: confira que chegaram **16** letras (sem contar espaços).
4. **Id do dono (opcional)** — o id numérico do Telegram da pessoa ([@userinfobot](https://t.me/userinfobot)).
   Se ficar vazio, quem mandar o primeiro `/start` vira o dono; nesse caso peça para a pessoa mandar `/start`
   logo que o bot subir, antes de divulgar o link.

## 2. Baixar e configurar

```sh
git clone https://github.com/fioravante-dev/kindle-telegram-bot.git
cd kindle-telegram-bot
cp .env.example .env && chmod 600 .env
```

Preencha `BOT_TOKEN`, `OWNER_ID`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `SMTP_USER`, `SMTP_PASSWORD` no
`.env` (sem aspas e sem espaços na senha). Para outro provedor de e-mail, `SMTP_HOST`/`SMTP_PORT`, e ajuste
`MAX_ATTACH_MB` ao limite dele (anexo ≈ 0,7 × limite da mensagem).

## 3A. Subir com Docker Compose

```sh
docker compose run --rm bot python3 /app/kindle_bot.py --logout-cloud   # espera {"ok":true,...}
docker compose up -d --build
docker compose run --rm bot python3 /app/kindle_bot.py --check
```

O `--logout-cloud` só precisa rodar uma vez por token. Se já foi feito, ele responde com erro; tudo bem.

## 3B. Subir sem Docker

```sh
sudo apt install -y --no-install-recommends calibre ghostscript qpdf python3-pil python3-pymupdf   # ou equivalente
# fora do Debian/Ubuntu: pip install pymupdf pillow
```

Servidor local da Bot API (exemplo com Podman/Docker, só em localhost):

```sh
mkdir -p ~/kindle-botapi
docker run -d --name kindle-botapi --restart unless-stopped -p 127.0.0.1:8081:8081 \
  -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=... -e TELEGRAM_LOCAL=1 \
  -v ~/kindle-botapi:/var/lib/telegram-bot-api aiogram/telegram-bot-api:latest
```

(passe as credenciais com `--env-file` em vez de linha de comando para não ficarem no histórico.)

No `.env`: `BOT_API_URL=http://127.0.0.1:8081`, `BOT_API_DIR_SERVER=/var/lib/telegram-bot-api`,
`BOT_API_DIR_LOCAL=<caminho absoluto de ~/kindle-botapi>`. O bot precisa conseguir mover arquivos dali:
rode-o como root ou ajuste as permissões do diretório.

```sh
python3 kindle_bot.py --logout-cloud
python3 kindle_bot.py --check
```

Deixe rodando como serviço: units de exemplo em `deploy/systemd/`. Sem systemd, use o mecanismo de
inicialização da máquina (cron `@reboot`, um laço `while true` num script de boot etc.) e garanta que o bot
reinicia se cair.

## 4. Verificar

- `--check` com tudo `[OK]` (avisos `[AVISO]` são aceitáveis se a pessoa entendeu o efeito).
- Teste de conversão, sem Telegram: `BOT_TOKEN=x python3 scripts/smoke_test.py` (no Docker:
  `docker compose run --rm bot python3 /app/scripts/smoke_test.py`). Deve imprimir três linhas `OK`.
- Log mostra `bot @<nome> no ar`.

## 5. Configurar a Amazon e testar com a pessoa

Para **cada** pessoa que vai usar:

1. Em [Seu conteúdo e dispositivos → Preferências](https://www.amazon.com.br/hz/mycd/myx#/home/settings/payment)
   (troque `.com.br` pelo país da conta), em **Configurações de documentos pessoais**:
   - copiar o **e-mail do Send to Kindle** (`algo@kindle.com`);
   - em **Lista de e-mails aprovados**, adicionar o `SMTP_USER` (ou `MAIL_FROM`).
2. No Telegram: abrir `https://t.me/<usuario_do_bot>` → **Iniciar**.
   - O dono entra direto. Outras pessoas ficam pendentes e o dono recebe **Aprovar/Recusar**.
3. Mandar `/kindle algo@kindle.com`.
4. Mandar um EPUB ou PDF pequeno **como arquivo**. Se for um PDF em A4, o bot mostra uma prévia e pergunta
   se ajusta à tela; mostre isso à pessoa e explique o `/pdf`. O bot responde ✅; o livro aparece no Kindle em alguns
   minutos com o Wi-Fi ligado.
   - Se não chegar: conferir a caixa de entrada do `SMTP_USER` (a Amazon manda erro ou pedido de
     confirmação para lá) e se o remetente está mesmo aprovado.

## 6. Entregar

Diga à pessoa, em poucas linhas:
- o link do bot e como outras pessoas pedem acesso (`/start` → ela aprova);
- que cada pessoa precisa aprovar o e-mail remetente na própria conta Amazon;
- comandos do dono (`/usuarios`, `/remover <id>`);
- onde ficam o log e o `.env`, e como reiniciar o serviço;
- que, para cortar o acesso ao e-mail, basta apagar a senha de app na conta Google.
