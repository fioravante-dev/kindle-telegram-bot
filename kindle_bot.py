#!/usr/bin/env python3
"""Bot do Telegram que entrega livros no Kindle pelo e-mail Send to Kindle.

Fluxo: arquivo (ou link) no chat -> download pela Bot API (de preferência um servidor local em modo
--local, que aceita arquivos de até 2 GB) -> conversão com o Calibre quando a Amazon não aceita o
formato -> redução/divisão quando passa do limite do e-mail -> envio por SMTP para o endereço
@kindle.com de quem mandou.

Só usa a biblioteca padrão do Python (+ Pillow, opcional, para recomprimir imagens de EPUB) e os
programas ebook-convert (Calibre), gs (Ghostscript) e qpdf.

Configuração por variáveis de ambiente (ou um arquivo KEY=VALUE apontado por KINDLE_BOT_ENV).
Ver .env.example. Uso:
  python3 kindle_bot.py               # roda o bot
  python3 kindle_bot.py --check       # confere configuração, Telegram, SMTP e ferramentas
  python3 kindle_bot.py --logout-cloud  # tira o bot da api.telegram.org (antes do servidor local)
"""
import json
import os
import queue
import re
import shutil
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


# ---------------------------------------------------------------- configuração
def _load_env_file(path):
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if os.environ.get("KINDLE_BOT_ENV"):
    _load_env_file(os.environ["KINDLE_BOT_ENV"])
elif Path(".env").is_file():
    _load_env_file(".env")


def cfg(name, default=None, *aliases):
    for n in (name, *aliases):
        v = os.environ.get(n)
        if v not in (None, ""):
            return v
    return default


TOKEN = cfg("BOT_TOKEN")
API_BASE = cfg("BOT_API_URL", "http://127.0.0.1:8081").rstrip("/")
# No modo --local o getFile devolve um caminho absoluto *dentro* do servidor da Bot API.
# Se o servidor roda em container, diga onde esse diretório aparece para o bot.
BOTAPI_DIR_SERVER = cfg("BOT_API_DIR_SERVER", "/var/lib/telegram-bot-api")
BOTAPI_DIR_LOCAL = cfg("BOT_API_DIR_LOCAL", BOTAPI_DIR_SERVER)
OWNER_ID = cfg("OWNER_ID")
SMTP_HOST = cfg("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(cfg("SMTP_PORT", "465"))
SMTP_USER = cfg("SMTP_USER", None, "GMAIL_USER")
SMTP_PASSWORD = (cfg("SMTP_PASSWORD", "", "GMAIL_APP_PASSWORD") or "").replace(" ", "")
MAIL_FROM = cfg("MAIL_FROM", SMTP_USER)
# O Gmail limita a mensagem a 25 MB e o anexo cresce ~37% em base64: ~17 MB de arquivo.
MAX_ATTACH = int(float(cfg("MAX_ATTACH_MB", "17")) * 1024 * 1024)
MAX_DOWNLOAD = int(float(cfg("MAX_DOWNLOAD_MB", "2048")) * 1024 * 1024)
DATA_DIR = Path(cfg("DATA_DIR", "./data")).resolve()
STATE_FILE = DATA_DIR / "users.json"
WORK_ROOT = DATA_DIR / "tmp"
OUTPUT_PROFILE = cfg("OUTPUT_PROFILE", "kindle_pw3")
COMIC_COLOR = cfg("COMIC_COLOR", "0") == "1"   # 1 = HQs coloridas (Kindle Colorsoft)

API = f"{API_BASE}/bot{TOKEN}"

# Formatos que o Send to Kindle aceita por e-mail (a Amazon converte sozinha).
DIRECT = {"epub", "pdf", "doc", "docx", "txt", "rtf", "htm", "html", "jpg", "jpeg", "png", "gif", "bmp"}
# Formatos que o Calibre converte para EPUB antes do envio.
CONVERT = {"mobi", "azw", "azw3", "azw4", "prc", "pdb", "fb2", "fbz", "lit", "lrf", "odt", "snb",
           "tcr", "txtz", "htmlz", "rb", "pml", "chm", "djvu", "docm", "md", "markdown",
           "cbz", "cbr", "cb7", "cbc"}
COMIC = {"cbz", "cbr", "cb7", "cbc"}
KINDLE_RE = re.compile(r"^[A-Za-z0-9._%+-]+@(free\.)?kindle\.(com|cn)$")
URL_RE = re.compile(r"^https?://\S+$")


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- estado
_state_lock = threading.Lock()
STATE = {"users": {}}


def load_state():
    global STATE
    try:
        STATE = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        STATE = {"users": {}}
    STATE.setdefault("users", {})


def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=1, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def owner():
    o = OWNER_ID or STATE.get("owner")
    return int(o) if o else None


def user(uid):
    return STATE["users"].get(str(uid))


def set_user(uid, **fields):
    with _state_lock:
        u = STATE["users"].setdefault(str(uid), {})
        u.update(fields)
        save_state()
        return u


def approved(uid):
    u = user(uid)
    return uid == owner() or (u is not None and u.get("status") == "approved")


# ---------------------------------------------------------------- Telegram
def tg(method, timeout=70, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        res = json.load(e)
    if not res.get("ok"):
        raise RuntimeError(f"{method}: {res.get('description')}")
    return res["result"]


def send(chat, text, **kw):
    try:
        return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                  disable_web_page_preview=True, **kw)
    except Exception as e:  # noqa: BLE001
        log("sendMessage falhou:", e)


def edit(chat, msg_id, text):
    try:
        tg("editMessageText", chat_id=chat, message_id=msg_id, text=text, parse_mode="HTML",
           disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        if "not modified" not in str(e):
            log("editMessageText falhou:", e)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mb(n):
    return f"{n / 1024 / 1024:.1f} MB"


def who(frm):
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
    if frm.get("username"):
        name += f" (@{frm['username']})"
    return name or str(frm["id"])


# ---------------------------------------------------------------- textos
def help_text():
    return (
        "📚 <b>Manda um livro que eu entrego no seu Kindle.</b>\n\n"
        "É só me enviar o arquivo (como <i>arquivo/documento</i>, não como foto) ou colar um link direto "
        "para ele. Aceito EPUB, PDF, DOCX, TXT, RTF, HTML, imagens e também MOBI, AZW3, FB2, CBZ/CBR, "
        "ODT, DJVU etc. (esses eu converto antes).\n\n"
        "<b>Comandos</b>\n"
        "/kindle <code>seu_nome@kindle.com</code>: define o seu Kindle\n"
        "/eu: mostra sua configuração\n"
        "/ajuda: esta mensagem\n\n"
        "<b>Dicas na legenda do arquivo</b>\n"
        "<code>epub</code>: converte para EPUB antes de mandar (bom para PDFs de texto e DOCX)\n"
        "<code>converter</code>: em PDF, pede para a Amazon refluir o texto (fica com letra ajustável)"
    )


def setup_text():
    return (
        "Falta configurar o Kindle:\n\n"
        "1. Na Amazon (<i>Conta → Seu conteúdo e dispositivos → Preferências → "
        "Configurações de documentos pessoais</i>), copie o <b>endereço de e-mail do Send to Kindle</b> "
        "(termina em <code>@kindle.com</code>).\n"
        f"2. Na mesma tela, em <i>Lista de e-mails aprovados</i>, adicione <code>{esc(MAIL_FROM)}</code>.\n"
        "3. Me mande: /kindle <code>seu_nome@kindle.com</code>"
    )


# ---------------------------------------------------------------- comandos
def handle_command(msg, text):
    chat = msg["chat"]["id"]
    frm = msg["from"]
    uid = frm["id"]
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()

    if cmd == "/start":
        if owner() is None:
            # Sem OWNER_ID: o primeiro a falar com o bot vira o dono.
            with _state_lock:
                STATE["owner"] = uid
                save_state()
            set_user(uid, status="approved", name=who(frm), chat=chat)
            send(chat, "👑 Você é o dono deste bot. Quem mais mandar /start vai precisar da sua aprovação.")
        if approved(uid):
            if not user(uid):
                set_user(uid, status="approved", name=who(frm), chat=chat)
            send(chat, help_text())
            if not (user(uid) or {}).get("kindle"):
                send(chat, setup_text())
            return
        u = user(uid)
        if u and u.get("status") == "blocked":
            return
        if u and u.get("status") == "pending":
            send(chat, "Seu pedido já foi enviado. Assim que for aprovado eu te aviso. ⏳")
            return
        set_user(uid, status="pending", name=who(frm), chat=chat)
        send(chat, "Oi! Este bot é particular. Pedi autorização ao dono, e te aviso assim que liberar. ⏳")
        kb = {"inline_keyboard": [[{"text": "✅ Aprovar", "callback_data": f"ok:{uid}"},
                                   {"text": "🚫 Recusar", "callback_data": f"no:{uid}"}]]}
        send(owner(), f"🔔 <b>{esc(who(frm))}</b> (id <code>{uid}</code>) quer usar o bot do Kindle.",
             reply_markup=kb)
        return

    if not approved(uid):
        send(chat, "Este bot é particular. Mande /start para pedir acesso.")
        return

    if cmd in ("/ajuda", "/help"):
        send(chat, help_text())
    elif cmd == "/kindle":
        if not KINDLE_RE.match(arg):
            send(chat, "Mande assim: /kindle <code>seu_nome@kindle.com</code>")
            return
        if not user(uid):
            set_user(uid, status="approved", name=who(frm), chat=chat)
        set_user(uid, kindle=arg.lower(), chat=chat)
        send(chat, f"✅ Kindle salvo: <code>{esc(arg.lower())}</code>\n\n"
                   f"Confirme que <code>{esc(MAIL_FROM)}</code> está na lista de e-mails "
                   "aprovados da Amazon. Agora é só me mandar livros.")
    elif cmd == "/eu":
        u = user(uid) or {}
        send(chat, f"Kindle: <code>{esc(u.get('kindle', 'não definido'))}</code>\n"
                   f"Enviados: {u.get('sent', 0)}")
    elif cmd == "/usuarios" and uid == owner():
        icon = {"approved": "✅", "pending": "⏳"}
        lines = [f"{icon.get(u.get('status'), '🚫')} <code>{k}</code> {esc(u.get('name', ''))}: "
                 f"{esc(u.get('kindle', '-'))}, {u.get('sent', 0)} enviados"
                 for k, u in STATE["users"].items()]
        send(chat, "\n".join(lines) or "Ninguém ainda.")
    elif cmd == "/remover" and uid == owner():
        if arg not in STATE["users"] or int(arg) == owner():
            send(chat, "Uso: /remover <code>id</code> (veja /usuarios)")
            return
        set_user(arg, status="blocked")
        send(chat, f"🚫 {esc(STATE['users'][arg].get('name', arg))} bloqueado.")
    else:
        send(chat, "Não conheço esse comando. /ajuda")


def handle_callback(cb):
    data = cb.get("data", "")
    if cb["from"]["id"] != owner() or ":" not in data:
        tg("answerCallbackQuery", callback_query_id=cb["id"])
        return
    action, target = data.split(":", 1)
    u = user(target)
    if not u:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text="Usuário não encontrado")
        return
    if action == "ok":
        set_user(target, status="approved")
        send(u["chat"], "🎉 Acesso liberado!\n\n" + help_text())
        send(u["chat"], setup_text())
        verdict = "✅ aprovado"
    else:
        set_user(target, status="blocked")
        verdict = "🚫 recusado"
    tg("answerCallbackQuery", callback_query_id=cb["id"], text=verdict)
    msg = cb.get("message")
    if msg:
        edit(msg["chat"]["id"], msg["message_id"], f"{esc(u.get('name', target))}: {verdict}")


# ---------------------------------------------------------------- arquivos
JOBS = queue.Queue()


def safe_name(name, default="documento"):
    name = os.path.basename(name or "").strip().replace("\x00", "")
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    return name[:150] or default


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def run(cmd, timeout=1800):
    # Temporários do Calibre/gs no disco de dados (o /tmp pode ser um tmpfs pequeno).
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", LANG="C.UTF-8", TMPDIR=str(WORK_ROOT))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} falhou: {(p.stderr or p.stdout)[-600:]}")
    return p.stdout


def fetch_telegram_file(file_id, dest_dir, name):
    info = tg("getFile", timeout=3600, file_id=file_id)
    src = info["file_path"]
    dest = dest_dir / name
    if src.startswith("/"):
        # Servidor local (--local): o arquivo já está no disco; só mover.
        if src.startswith(BOTAPI_DIR_SERVER):
            src = BOTAPI_DIR_LOCAL + src[len(BOTAPI_DIR_SERVER):]
        shutil.move(src, dest)
        return dest
    with urllib.request.urlopen(f"{API_BASE}/file/bot{TOKEN}/{src}", timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def fetch_url(url, dest_dir):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (kindle-telegram-bot)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        name = None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
        if m:
            name = urllib.parse.unquote(m.group(1).strip())
        if not name:
            name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(r.url).path))
        name = safe_name(name, "documento")
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        if ext_of(name) not in DIRECT | CONVERT:
            guess = {"application/pdf": "pdf", "application/epub+zip": "epub",
                     "text/html": "html", "text/plain": "txt"}.get(ctype)
            if guess:
                name = f"{name.rsplit('.', 1)[0] if '.' in name else name}.{guess}"
        dest = dest_dir / name
        total = 0
        with open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise RuntimeError(f"o arquivo do link passa de {mb(MAX_DOWNLOAD)}")
                f.write(chunk)
    return dest


# ---------------------------------------------------------------- conversão
COMIC_OPTS = [f"--output-profile={OUTPUT_PROFILE}", "--output-format=jpg"] + (["--dont-grayscale"] if COMIC_COLOR else [])


def calibre(src, dst, *extra):
    run(["ebook-convert", str(src), str(dst), *extra], timeout=3600)
    return dst


def shrink_pdf(src, work):
    (work / "min").mkdir(exist_ok=True)
    out = work / "min" / src.name
    run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
         "-dPDFSETTINGS=/ebook", "-dDetectDuplicateImages=true", f"-sOutputFile={out}", str(src)], timeout=3600)
    return out if out.stat().st_size < src.stat().st_size else src


def split_pdf(src, work):
    pages = int(run(["qpdf", "--show-npages", str(src)]).strip())
    n = min(pages, max(2, -(-src.stat().st_size // int(MAX_ATTACH * 0.85))))
    while True:
        per = max(1, -(-pages // n))
        n = -(-pages // per)
        parts = []
        for i in range(n):
            a, b = i * per + 1, min((i + 1) * per, pages)
            out = work / f"{src.stem} (parte {i + 1} de {n}).pdf"
            run(["qpdf", "--empty", "--pages", str(src), f"{a}-{b}", "--", str(out)])
            parts.append(out)
        if all(p.stat().st_size <= MAX_ATTACH for p in parts) or per == 1:
            return parts
        for p in parts:
            p.unlink()
        n *= 2


def shrink_epub_images(src, work, max_side=1600, quality=70):
    """Recomprime as imagens de um EPUB (JPEG q70, lado máx. 1600 px), mantendo nomes e tipos."""
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        return src
    (work / "min").mkdir(exist_ok=True)
    out = work / "min" / src.name
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            low = item.filename.lower()
            if low.endswith((".jpg", ".jpeg", ".png")) and len(data) > 100_000:
                try:
                    im = Image.open(BytesIO(data))
                    im.thumbnail((max_side, max_side))
                    buf = BytesIO()
                    if low.endswith(".png"):
                        im.save(buf, "PNG", optimize=True)
                    else:
                        im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
                    if buf.tell() < len(data):
                        data = buf.getvalue()
                except Exception:  # noqa: BLE001
                    pass
            ctype = zipfile.ZIP_STORED if item.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(item.filename, data, compress_type=ctype)
    return out if out.stat().st_size < src.stat().st_size else src


def split_comic(src, work):
    """Divide um CBZ em partes pelo número de páginas e converte cada uma para EPUB."""
    with zipfile.ZipFile(src) as z:
        imgs = sorted(n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")))
        n = max(2, -(-src.stat().st_size // int(MAX_ATTACH * 0.8)))
        while True:
            per = max(1, -(-len(imgs) // n))
            n = -(-len(imgs) // per)
            parts = []
            for i in range(n):
                cbz = work / f"{src.stem} (parte {i + 1} de {n}).cbz"
                with zipfile.ZipFile(cbz, "w") as zo:
                    for name in imgs[i * per:(i + 1) * per]:
                        zo.writestr(name.replace("/", "_"), z.read(name))
                parts.append(calibre(cbz, cbz.with_suffix(".epub"), *COMIC_OPTS,
                                     "--title", f"{src.stem} ({i + 1}/{n})"))
                cbz.unlink()
            if all(p.stat().st_size <= MAX_ATTACH for p in parts) or per == 1:
                return parts
            for p in parts:
                p.unlink()
            n *= 2


def prepare(path, work, caption, status):
    """Devolve (arquivos para enviar, assunto do e-mail)."""
    ext = original_ext = ext_of(path.name)
    original = path
    cap = (caption or "").lower()
    subject = "Convert" if ("convert" in cap and ext == "pdf") else ""

    if ext not in DIRECT | CONVERT:
        raise RuntimeError(f"não sei o que fazer com arquivos .{esc(ext) or '(sem extensão)'}")

    if ext in CONVERT or ("epub" in cap.split() and ext in {"pdf", "docx", "doc", "txt", "rtf", "html", "htm"}):
        status(f"🔄 Convertendo <b>{esc(path.name)}</b> para EPUB…")
        opts = COMIC_OPTS if ext in COMIC else [f"--output-profile={OUTPUT_PROFILE}"]
        path, ext = calibre(path, work / f"{path.stem}.epub", *opts), "epub"

    if path.stat().st_size <= MAX_ATTACH:
        return [path], subject

    status(f"🗜 {esc(path.name)} tem {mb(path.stat().st_size)}, acima do limite do e-mail. Reduzindo…")
    if ext == "pdf":
        path = shrink_pdf(path, work)
        if path.stat().st_size <= MAX_ATTACH:
            return [path], subject
        status(f"✂️ Ainda com {mb(path.stat().st_size)}. Dividindo o PDF em partes…")
        return split_pdf(path, work), subject
    if ext == "epub":
        path = shrink_epub_images(path, work)
        if path.stat().st_size <= MAX_ATTACH:
            return [path], subject
    if original_ext == "cbz":
        status(f"✂️ Ainda com {mb(path.stat().st_size)}. Dividindo a HQ em partes…")
        parts = split_comic(original, work)
        if all(p.stat().st_size <= MAX_ATTACH for p in parts):
            return parts, subject
        path = max(parts, key=lambda p: p.stat().st_size)
    raise RuntimeError(
        f"mesmo reduzido, o arquivo ficou com {mb(path.stat().st_size)}, e o e-mail só leva até ~{mb(MAX_ATTACH)}. "
        "Use o app ou o site Send to Kindle (amazon.com/sendtokindle), que aceita até 200 MB.")


# ---------------------------------------------------------------- e-mail
def smtp_connect():
    if SMTP_PORT == 465:
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=300)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=300)
        s.starttls()
    s.login(SMTP_USER, SMTP_PASSWORD)
    return s


def send_mail(to, files, subject):
    if not (SMTP_USER and SMTP_PASSWORD):
        raise RuntimeError("o envio por e-mail não está configurado (SMTP_USER/SMTP_PASSWORD)")
    with smtp_connect() as s:
        for f in files:
            m = EmailMessage()
            m["From"] = MAIL_FROM
            m["To"] = to
            m["Subject"] = subject or f.stem
            m["Date"] = formatdate(localtime=True)
            m["Message-ID"] = make_msgid()
            m.set_content("Enviado pelo kindle-telegram-bot.")
            m.add_attachment(f.read_bytes(), maintype="application", subtype="octet-stream", filename=f.name)
            s.send_message(m)


# ---------------------------------------------------------------- trabalho
def process(job):
    chat, uid, msg_id = job["chat"], job["uid"], job["reply_to"]
    u = user(uid) or {}
    st = send(chat, "📥 Recebido, baixando…", reply_parameters={"message_id": msg_id}) or {}
    st_id = st.get("message_id")

    def status(t):
        if st_id:
            edit(chat, st_id, t)

    work = Path(tempfile.mkdtemp(prefix="job-", dir=WORK_ROOT))
    try:
        if job.get("file_id"):
            path = fetch_telegram_file(job["file_id"], work, safe_name(job["name"]))
        else:
            path = fetch_url(job["url"], work)
        log(f"job uid={uid} arquivo={path.name} tamanho={path.stat().st_size}")
        files, subject = prepare(path, work, job.get("caption"), status)
        total = len(files)
        status(f"📤 Enviando {'em ' + str(total) + ' partes ' if total > 1 else ''}para "
               f"<code>{esc(u['kindle'])}</code>…")
        send_mail(u["kindle"], files, subject)
        set_user(uid, sent=u.get("sent", 0) + 1)
        names = "\n".join(f"• {esc(f.name)} ({mb(f.stat().st_size)})" for f in files)
        status(f"✅ Enviado para o Kindle:\n{names}\n\nDeve aparecer em alguns minutos (com Wi-Fi ligado).")
    except Exception as e:  # noqa: BLE001
        log("erro no job:", traceback.format_exc())
        status(f"❌ Não consegui: {esc(str(e)[:700])}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def worker():
    while True:
        job = JOBS.get()
        try:
            process(job)
        except Exception:  # noqa: BLE001
            log("worker:", traceback.format_exc())


def handle_message(msg):
    chat = msg["chat"]["id"]
    if msg["chat"].get("type") != "private" or "from" not in msg:
        return
    uid = msg["from"]["id"]
    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        handle_command(msg, text)
        return
    if not approved(uid):
        send(chat, "Este bot é particular. Mande /start para pedir acesso.")
        return
    u = user(uid) or {}
    if not u.get("kindle"):
        send(chat, setup_text())
        return

    job = {"chat": chat, "uid": uid, "reply_to": msg["message_id"], "caption": msg.get("caption")}
    if doc := msg.get("document"):
        job.update(file_id=doc["file_id"], name=doc.get("file_name") or "documento")
    elif URL_RE.match(text):
        job.update(url=text)
    elif msg.get("photo"):
        send(chat, "Mande a imagem como <b>arquivo</b> (📎 → Arquivo), senão o Telegram a comprime.")
        return
    else:
        send(chat, "Mande um arquivo ou um link direto para ele. /ajuda")
        return
    if pos := JOBS.qsize():
        send(chat, f"Na fila ({pos} antes do seu).")
    JOBS.put(job)


# ---------------------------------------------------------------- CLI
def check():
    """Confere tudo o que o bot precisa. Sai com código 1 se algo essencial falhar."""
    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        ok &= good is not False
        mark = {True: "OK  ", False: "FALHA", None: "AVISO"}[good]
        print(f"[{mark}] {label}{': ' + detail if detail else ''}")

    line(bool(TOKEN), "BOT_TOKEN definido")
    try:
        me = tg("getMe", timeout=15)
        line(True, f"Bot API em {API_BASE}", f"@{me['username']}")
    except Exception as e:  # noqa: BLE001
        line(False, f"Bot API em {API_BASE}", str(e)[:200])
    if "api.telegram.org" in API_BASE:
        line(None, "servidor da nuvem", "arquivos limitados a 20 MB; use o servidor local para até 2 GB")
    line(bool(SMTP_USER and SMTP_PASSWORD), "SMTP_USER/SMTP_PASSWORD definidos")
    if SMTP_USER and SMTP_PASSWORD:
        try:
            smtp_connect().quit()
            line(True, f"login SMTP em {SMTP_HOST}:{SMTP_PORT}", SMTP_USER)
        except smtplib.SMTPAuthenticationError as e:
            line(False, "login SMTP", f"{e.smtp_code}: usuário/senha recusados (no Gmail, use uma senha de app)")
        except Exception as e:  # noqa: BLE001
            line(False, "login SMTP", f"{type(e).__name__}: {e}")
    for tool, why in (("ebook-convert", "Calibre (conversão)"), ("gs", "Ghostscript (reduzir PDF)"),
                      ("qpdf", "qpdf (dividir PDF)")):
        line(shutil.which(tool) is not None or (None if tool != "ebook-convert" else False), tool, why)
    try:
        import PIL  # noqa: F401
        line(True, "Pillow", "recomprimir imagens de EPUB")
    except ImportError:
        line(None, "Pillow", "ausente: EPUBs grandes não serão reduzidos")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / ".w").write_text("x")
        (DATA_DIR / ".w").unlink()
        line(True, "DATA_DIR gravável", str(DATA_DIR))
    except Exception as e:  # noqa: BLE001
        line(False, "DATA_DIR gravável", str(e))
    line(True if OWNER_ID else None, "OWNER_ID",
         OWNER_ID or "vazio: o primeiro a mandar /start vira o dono")
    return 0 if ok else 1


def logout_cloud():
    url = f"https://api.telegram.org/bot{TOKEN}/logOut"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=b""), timeout=30) as r:
            print(json.load(r))
    except urllib.error.HTTPError as e:
        print(json.load(e))


def main():
    if not TOKEN:
        sys.exit("BOT_TOKEN não definido (veja .env.example)")
    if "--check" in sys.argv:
        sys.exit(check())
    if "--logout-cloud" in sys.argv:
        logout_cloud()
        return
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    for old in WORK_ROOT.glob("job-*"):
        shutil.rmtree(old, ignore_errors=True)
    load_state()
    me = tg("getMe")
    log(f"bot @{me['username']} no ar; api={API_BASE}; dono={owner()}; usuários={len(STATE['users'])}")
    tg("setMyCommands", commands=[
        {"command": "kindle", "description": "Definir seu e-mail @kindle.com"},
        {"command": "eu", "description": "Ver sua configuração"},
        {"command": "ajuda", "description": "Como usar"},
    ])
    threading.Thread(target=worker, daemon=True).start()
    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            for upd in tg("getUpdates", **params):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        handle_message(upd["message"])
                    elif "callback_query" in upd:
                        handle_callback(upd["callback_query"])
                except Exception:  # noqa: BLE001
                    log("update:", traceback.format_exc())
        except Exception as e:  # noqa: BLE001
            log("getUpdates:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
