# kindle-telegram-bot

Um bot do Telegram que entrega livros no Kindle. Você manda o arquivo (ou um link) no chat; o bot
converte se precisar e envia para o e-mail **Send to Kindle** de quem mandou. Várias pessoas podem usar o
mesmo bot (cada uma com o seu Kindle); o dono aprova quem entra.

- **Formatos:** EPUB, PDF, DOCX, TXT, RTF, HTML e imagens vão direto (a Amazon aceita). MOBI, AZW3, FB2,
  CBZ/CBR, ODT, DJVU, LIT etc. são convertidos para EPUB com o [Calibre](https://calibre-ebook.com).
- **Arquivos grandes:** com o servidor local da Bot API, o Telegram aceita até 2 GB. O limite é o e-mail
  (Gmail: 25 MB por mensagem, ~17 MB de anexo), então o bot reduz PDFs (Ghostscript) e, se ainda não couber,
  divide em partes (qpdf); recomprime imagens de EPUBs e divide HQs (CBZ) em partes.
- **Legenda do arquivo:** `epub` força a conversão para EPUB (bom para PDF de texto); `converter` manda um
  PDF com o assunto "Convert", e a Amazon reflui o texto.
- **Links:** colar um link direto para um arquivo funciona igual a mandar o arquivo.

> 🤖 Quer que um agente de IA (Claude Code, Codex etc.) faça o setup por você? Mande ele seguir
> [`docs/setup-com-agente.md`](docs/setup-com-agente.md).

## Como funciona

```
Telegram ──► telegram-bot-api (local, --local) ──► kindle_bot.py ──► Calibre / gs / qpdf ──► SMTP ──► @kindle.com
```

`kindle_bot.py` é um arquivo só, com a biblioteca padrão do Python (Pillow é opcional). Os usuários ficam em
`DATA_DIR/users.json`.

## O que você precisa

| O quê | Onde | Para quê |
|---|---|---|
| Token do bot | [@BotFather](https://t.me/BotFather) → `/newbot` | o bot em si |
| `api_id` e `api_hash` | [my.telegram.org/apps](https://my.telegram.org/apps) | servidor local da Bot API (arquivos até 2 GB) |
| Conta de e-mail com SMTP | Gmail: [senha de app](https://myaccount.google.com/apppasswords) | enviar para o Kindle |
| Uma máquina sempre ligada | Linux com Docker, ou Python 3.10+ e Calibre | rodar tudo |

Sobre o Gmail: a senha de app exige a **verificação em duas etapas** ligada. Contas recém-criadas às vezes
não conseguem criar senha de app por alguns dias ("Não foi possível confirmar sua identidade"); use uma conta
mais antiga. Outros provedores funcionam com `SMTP_HOST`/`SMTP_PORT`.

## Instalação com Docker (recomendado)

```sh
git clone https://github.com/fioravante-dev/kindle-telegram-bot.git
cd kindle-telegram-bot
cp .env.example .env        # preencha BOT_TOKEN, TELEGRAM_API_ID/HASH, SMTP_USER/PASSWORD (e OWNER_ID)
docker compose run --rm bot python3 /app/kindle_bot.py --logout-cloud   # uma vez só, ver abaixo
docker compose up -d --build
docker compose run --rm bot python3 /app/kindle_bot.py --check
docker compose logs -f bot
```

O `--logout-cloud` tira o bot dos servidores do Telegram (`api.telegram.org`), o que é obrigatório para
usar um servidor local. Para voltar à nuvem um dia, chame `logOut` no servidor local e espere 10 minutos.

## Instalação sem Docker

```sh
sudo apt install calibre ghostscript qpdf python3-pil   # Debian/Ubuntu
cp .env.example .env && $EDITOR .env
```

Servidor local da Bot API: rode o container `aiogram/telegram-bot-api` (ou
[compile](https://github.com/tdlib/telegram-bot-api)) com `--local`, escutando em `127.0.0.1:8081`. Se ele
roda em container e o bot fora, monte o diretório de trabalho dele no host e configure
`BOT_API_DIR_SERVER` (caminho dentro do container) e `BOT_API_DIR_LOCAL` (caminho no host). Exemplos de
units do systemd em [`deploy/systemd/`](deploy/systemd).

Sem servidor local: `BOT_API_URL=https://api.telegram.org` funciona, mas arquivos acima de 20 MB são
recusados pelo Telegram.

```sh
python3 kindle_bot.py --logout-cloud   # só se for usar o servidor local
python3 kindle_bot.py --check
python3 kindle_bot.py
```

## Primeiro uso

1. **Dono:** mande `/start` para o bot. Se `OWNER_ID` estiver vazio, quem mandar o primeiro `/start` vira o dono.
2. **Cada pessoa, na Amazon:** *Conta → Seu conteúdo e dispositivos → Preferências → Configurações de
   documentos pessoais* ([link direto](https://www.amazon.com.br/hz/mycd/myx#/home/settings/payment)):
   - copie o **e-mail do Send to Kindle** (`algo@kindle.com`);
   - em **Lista de e-mails aprovados**, adicione o `SMTP_USER`/`MAIL_FROM` do bot.
3. **Cada pessoa, no Telegram:** abre `https://t.me/<seu_bot>`, manda `/start` (o dono recebe
   **Aprovar/Recusar**), depois `/kindle algo@kindle.com`, e manda um livro como arquivo.

Comandos: `/kindle`, `/eu`, `/ajuda`. Só o dono: `/usuarios`, `/remover <id>`.

## Configuração

Tudo por variáveis de ambiente, ou um arquivo `.env` no diretório atual, ou um arquivo apontado por
`KINDLE_BOT_ENV`. Veja [`.env.example`](.env.example).

| Variável | Padrão | |
|---|---|---|
| `BOT_TOKEN` | — | obrigatório |
| `OWNER_ID` | vazio | id numérico do dono ([@userinfobot](https://t.me/userinfobot)); vazio = primeiro `/start` |
| `SMTP_USER`, `SMTP_PASSWORD` | — | obrigatórios para enviar |
| `SMTP_HOST`, `SMTP_PORT` | `smtp.gmail.com`, `465` | `587` usa STARTTLS |
| `MAIL_FROM` | `SMTP_USER` | o endereço que as pessoas aprovam na Amazon |
| `MAX_ATTACH_MB` | `17` | acima disso o bot reduz/divide |
| `BOT_API_URL` | `http://127.0.0.1:8081` | `https://api.telegram.org` = sem servidor local |
| `BOT_API_DIR_SERVER`, `BOT_API_DIR_LOCAL` | `/var/lib/telegram-bot-api` | ver "sem Docker" |
| `DATA_DIR` | `./data` | `users.json` e temporários |
| `OUTPUT_PROFILE` | `kindle_pw3` | perfil de saída do Calibre |
| `COMIC_COLOR` | `0` | `1` mantém HQs coloridas (Kindle Colorsoft) |

## Problemas comuns

- **Nada chega no Kindle:** o remetente não está na lista de aprovados da Amazon, ou o endereço `@kindle.com`
  está errado (`/eu`). A Amazon manda e-mails de erro, e às vezes de "confirme o envio", para a caixa do
  `SMTP_USER`.
- **`--check` falha no login SMTP (535):** a senha não é de app, ou está incompleta (são 16 letras).
- **`Conflict: terminated by other getUpdates request`:** há duas instâncias do bot com o mesmo token.
- **O bot não recebe mensagens com o servidor local:** faltou o `--logout-cloud` (uma vez por token).
- **Teste sem Telegram:** `BOT_TOKEN=x python3 scripts/smoke_test.py` (ou `docker compose run --rm bot python3
  /app/scripts/smoke_test.py`) converte, reduz e divide arquivos gerados.

## Licença

MIT.
