#!/usr/bin/env python3
"""Testa a conversão/redução/divisão sem Telegram nem e-mail.

Uso: BOT_TOKEN=x python3 scripts/smoke_test.py
Gera um livro em MOBI, um PDF grande e uma HQ grande e roda o mesmo pipeline do bot,
com um limite de anexo de 3 MB para forçar a divisão.
"""
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "x")
os.environ["MAX_ATTACH_MB"] = "3"
tmp = Path(tempfile.mkdtemp(prefix="kindle-smoke-", dir=os.environ.get("SMOKE_DIR")))
os.environ["DATA_DIR"] = str(tmp / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kindle_bot as kb  # noqa: E402
from PIL import Image  # noqa: E402

kb.WORK_ROOT.mkdir(parents=True, exist_ok=True)


def noise(w, h):
    return Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))


src = tmp / "src"
src.mkdir()
(src / "livro.html").write_text("<html><head><title>Livro Teste</title></head><body>" + "".join(
    f"<h1>Capítulo {i}</h1><p>{'Texto de teste. ' * 300}</p>" for i in range(10)) + "</body></html>")
kb.calibre(src / "livro.html", src / "livro.mobi")
with zipfile.ZipFile(src / "hq.cbz", "w", zipfile.ZIP_STORED) as z:
    for i in range(8):
        noise(1000, 1500).save(src / "p.jpg", quality=85)
        z.write(src / "p.jpg", f"{i:03d}.jpg")
pages = [noise(1000, 1400) for _ in range(6)]
pages[0].save(src / "grande.pdf", save_all=True, append_images=pages[1:], quality=95)

# PDF A4 em duas colunas, para o ajuste à tela
import pymupdf  # noqa: E402
a4 = pymupdf.open()
for _ in range(2):
    pg = a4.new_page(width=595, height=842)
    for x in (40, 310):
        pg.insert_textbox(pymupdf.Rect(x, 40, x + 245, 800), "Texto em coluna. " * 180, fontsize=9)
a4.save(src / "colunas.pdf")
work = Path(tempfile.mkdtemp(dir=kb.WORK_ROOT))
_, info = kb.pdf_for_kindle(src / "colunas.pdf", work)
ok = bool(info) and info["two_cols"] == 2 and info["pages_out"] > info["pages_in"]
print(f"{'OK   ' if ok else 'FALHA'} colunas.pdf -> ajuste à tela: {info}")

failed = not ok
for name in ("livro.mobi", "grande.pdf", "hq.cbz"):
    work = Path(tempfile.mkdtemp(dir=kb.WORK_ROOT))
    shutil.copy(src / name, work / name)
    try:
        files, _ = kb.prepare(work / name, work, None, lambda s: None)
        too_big = [f for f in files if f.stat().st_size > kb.MAX_ATTACH]
        print(f"{'OK   ' if not too_big else 'FALHA'} {name} -> " +
              ", ".join(f"{f.name} ({kb.mb(f.stat().st_size)})" for f in files))
        failed |= bool(too_big)
    except Exception as e:  # noqa: BLE001
        print(f"FALHA {name}: {e}")
        failed = True
shutil.rmtree(tmp)
sys.exit(1 if failed else 0)
