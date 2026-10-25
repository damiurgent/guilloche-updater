# -*- coding: utf-8 -*-
"""
gisjkh-ufo2.py — вторая итерация.

Правка 1: ветка «нет ссылки Информация об управлении МКД» → О.

Правка 2: при ошибке ZIP-скачивания в ветке УФО — fallback пофайлово.

Правка 3 (коллаж): вместо двух скринов договора (первая и последняя
    страницы) делается один скрин-коллаж 4x1 из 4 страниц.
    OCR каждой страницы — до склейки. Блок Н/Ж — 4 строки.

Правка 4 (баннер): перед snapshot «01_mkd» на карточке дома / управлении
    МКД страница прокручивается так, чтобы жёлтый баннер о регламентных
    работах ушёл за верх вьюпорта. Баннер НЕ удаляется из DOM, поэтому
    в iTXt-чанке Description его текст сохраняется.
"""

import os
import re
import time
import random
import shutil
import base64
import subprocess
import datetime as _dt
import urllib.parse
from pathlib import Path
from tempfile import gettempdir

from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from PIL.PngImagePlugin import PngInfo

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
IN_DIR = BASE_DIR / "IN"
OLD_DIR = BASE_DIR / "OLD"
IMG_DIR = BASE_DIR / "IMG"
LOG_DIR = BASE_DIR / "LOG"

DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads"
WINRAR = r"C:\Program Files\WinRAR\WinRAR.exe"

for d in (BASE_DIR, IN_DIR, OLD_DIR, IMG_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ============================== НАСТРОЙКИ ==============================
OGRN_FILE = Path(__file__).resolve().parent / "ogrns.txt"

GIS_URL = "https://my.dom.gosuslugi.ru/#!/houses"
UFO_URL = "https://e-trust.gosuslugi.ru/check/sign"

LOG_FILE = None
CONTRACT_LOG = None
SEQ_LOG = LOG_DIR / "sequence2.txt"

ufo_handle = None
gis_handle = None

COPYRIGHT = "Copyright 2026 \u00a9 damiurg by \u043c\u043b\u0445/\u05de\u05dc\u05da Living Private Trust"
TZ_NAME = "Asia/Omsk"
PNG_BITS = 8
OCR_LANG = "rus+eng"

WAIT_TIMEOUT = 25
PAUSE_BETWEEN_PAGES = (2, 4)
PAUSE_BETWEEN_UK = (5, 10)

EP_PREFIX_RE = re.compile(
    r"^Электронная подпись\s+оператора\s+ГИС\s+ЖКХ\s+\d{2}\.\d{2}\.\d{4}\s+",
    flags=re.IGNORECASE
)

CONTRACT_RE = re.compile(
    r"договор|(?<![а-яё])дог(?![а-яё])|dog|(?<![а-яё])ду(?![а-яё])",
    re.IGNORECASE
)

ADDENDUM_RE = re.compile(
    r"к\s*ду|к\s*договор|акт|доп|тариф|прот",
    re.IGNORECASE
)

RE_FACTUAL = re.compile(r"^(.*?)\s*\(\s*\d")
PDF_EXT_RE = re.compile(r"\.pdf", re.IGNORECASE)

_RE_REALNAME = re.compile(
    r"^([^\s(]+\.(?:pdf|sig|p7s))\s*(?:\(|$)",
    re.IGNORECASE,
)

TOTAL_RECORDS_RE = re.compile(r"Всего\s+записей[:\s]*(\d+)", re.IGNORECASE)

FB_WAIT_RENDER = 5
FB_WAIT_AFTER_CLICK = 7
FB_PDF_MARKERS = ("договор", "ду", "дог", "dog")

PDF_W = 1920
PDF_H = 953
PDF_DPI = 150
PDF_STUB_THRESHOLD = 5 * 1024

STUB_FONT_PATH = r"C:\Windows\Fonts\cour.ttf"
STUB_MAX_FONT_SIZE = 28
STUB_MIN_FONT_SIZE = 8
STUB_MARGIN_X = 30
STUB_MARGIN_Y = 30
STUB_LINE_SPACING = 1.15

SCR_RE = re.compile(r"^scr(\d{4,})\.png$")

pytesseract.pytesseract.tesseract_cmd = \
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ============================== НОРМАЛИЗАЦИЯ ИМЁН ==============================
def _norm_name(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\u00a0", " ") \
         .replace("\u2009", " ") \
         .replace("\u200a", " ") \
         .replace("\u202f", " ") \
         .replace("\u2007", " ") \
         .replace("\u2060", "") \
         .replace("\t", " ").replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ============================== ЛОГ ==============================
def setup_logs(ogrn: str):
    global LOG_FILE, CONTRACT_LOG
    LOG_FILE = LOG_DIR / f"gisjkh-ufo{ogrn}.log"
    CONTRACT_LOG = LOG_DIR / f"contracts{ogrn}.log"

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if LOG_FILE is None:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:
        print(f"[!] cannot write log: {e}", flush=True)

def log_line(filename_stem: str, date_str: str, result: str):
    log(f"    [result] {filename_stem} | {date_str} | {result}")

def log_already_written(filename_stem: str) -> bool:
    if LOG_FILE is None or not LOG_FILE.exists():
        return False
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                if f"[result] {filename_stem} |" in line:
                    return True
    except Exception:
        pass
    return False

# ============================== НОРМАЛИЗАЦИЯ ОРГ ==============================
def _normalize_org(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ",
               "ООО", s, flags=re.IGNORECASE)
    s = re.sub(r"АКЦИОНЕРНОЕ\s+ОБЩЕСТВО",
               "АО", s, flags=re.IGNORECASE)
    s = re.sub(r"УПРАВЛЯЮЩАЯ\s+КОМПАНИЯ",
               "УК", s, flags=re.IGNORECASE)
    s = re.sub(r"ЖИЛИЩНО-ЭКСПЛУАТАЦИОННОЕ\s+УПРАВЛЕНИЕ",
               "ЖЭУ", s, flags=re.IGNORECASE)
    s = re.sub(r"ЖИЛИЩНО-КОММУНАЛЬНОЕ\s+ХОЗЯЙСТВО",
               "ЖКХ", s, flags=re.IGNORECASE)
    return s.strip()

def contract_log_written(org: str, date_str: str,
                         pdf_name: str, addr: str) -> bool:
    if CONTRACT_LOG is None or not CONTRACT_LOG.exists():
        return False
    if not (org and date_str and pdf_name and addr):
        return False
    try:
        with open(CONTRACT_LOG, "r", encoding="utf-8") as fh:
            for line in fh:
                if not (org in line and date_str in line
                        and pdf_name in line and addr in line):
                    continue
                esc_pdf = re.escape(pdf_name)
                esc_date = re.escape(date_str)
                esc_org = re.escape(org)
                esc_addr = re.escape(addr)
                pattern = (
                    r"^-?\s*"
                    r"scr\d{4,}\.png\s*-\s*"
                    + esc_org +
                    r"\s*--\s*"
                    + esc_addr +
                    r"\s*---\s*"
                    + esc_pdf +
                    r"\s*----\s*"
                    + esc_date +
                    r"\s*$"
                )
                if re.match(pattern, line.strip()):
                    return True
    except Exception:
        pass
    return False

def write_contract_log(scr_a: str, scr_b: str, scr_c: str,
                       org: str, addr: str, pdf_name: str, date_str: str,
                       synopsis: str, marker: str,
                       scr_d: str = "", scr_e: str = "",
                       delta_str: str = ""):
    delta_part = delta_str if delta_str else "(0)-0=0"
    line1 = f"{scr_a} - {org} -- {addr} --- {pdf_name} ---- {date_str}"
    line2 = f"{scr_b} - {delta_part} -- {synopsis}"
    line3 = f"{scr_c} - {marker}"

    try:
        CONTRACT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTRACT_LOG, "a", encoding="utf-8", newline="") as fh:
            fh.write(line1 + "\r\n")
            fh.write(line2 + "\r\n")
            fh.write(line3 + "\r\n")
            if scr_d:
                fh.write(scr_d + "\r\n")
            if scr_e:
                fh.write(scr_e + "\r\n")
            fh.write("\r\n")
        log(f"    [contract-log] written: {line3} "
            f"| d={scr_d or '-'} e={scr_e or '-'} | delta={delta_part}")
    except Exception as e:
        print(f"[!] cannot write contract log: {e}", flush=True)

def write_contract_log_EO(scr_a: str, org: str, addr: str,
                          pdf_label: str, date_label: str,
                          synopsis: str, marker: str,
                          delta_str: str = ""):
    delta_part = delta_str if delta_str else "(0)-0=0"
    line1 = f"{scr_a} - {org} -- {addr} --- {pdf_label} ---- {date_label}"
    line2 = f"{scr_a} - {delta_part} -- {synopsis}"
    line3 = f"{scr_a} - {marker}"
    try:
        CONTRACT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTRACT_LOG, "a", encoding="utf-8", newline="") as fh:
            fh.write(line1 + "\r\n")
            fh.write(line2 + "\r\n")
            fh.write(line3 + "\r\n")
            fh.write("\r\n")
        log(f"    [contract-log-EO] written: {line3} | delta={delta_part}")
    except Exception as e:
        print(f"[!] cannot write contract log EO: {e}", flush=True)

# ============================== СЕКВЕНЦИЯ ==============================
def _scan_max_seq() -> int:
    maxn = 0
    try:
        for f in IMG_DIR.iterdir():
            if not f.is_file():
                continue
            m = SCR_RE.match(f.name)
            if m:
                maxn = max(maxn, int(m.group(1)))
    except Exception:
        pass
    return maxn

def _register_seq(scr_name: str, file_stem: str, tag: str):
    try:
        with open(SEQ_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{scr_name} | {file_stem} | {tag}\n")
    except Exception as e:
        print(f"[!] cannot write sequence log: {e}")

# ============================== iTXt ==============================
def _build_itxt_meta(title: str, comment: str, description: str,
                     extra_source: str = "") -> PngInfo:
    now = _dt.datetime.now(ZoneInfo(TZ_NAME))
    dt_str = now.strftime("%Y-%m-%d %H:%M:%S")
    tz_str = now.strftime("%z")
    tz_fmt = f"{tz_str[0]}{tz_str[1:3]}:{tz_str[3:5]}" if tz_str else ""

    meta = PngInfo()
    meta.add_itxt("Title", title, zip=False)
    meta.add_itxt("Author", COPYRIGHT, zip=False)
    meta.add_itxt("Copyright", COPYRIGHT, zip=False)
    meta.add_itxt("Software", "gisjkh-ufo2", zip=False)
    meta.add_itxt("Creation Time", f"{dt_str} {tz_fmt}", zip=False)
    if extra_source:
        meta.add_itxt("Source", extra_source, zip=False)
    meta.add_itxt("Comment", comment, zip=False)
    meta.add_itxt("Description", description or "", zip=False)
    return meta

# ============================== ОЧИСТКА OCR ==============================
_RE_CYR = re.compile(r"[А-Яа-яЁё]")
_RE_LAT = re.compile(r"[A-Za-z]")
_RE_DATE = re.compile(r"\d{2}[.\-/]\d{2}[.\-/]\d{4}")
_RE_ADDR_KEY = re.compile(
    r"(?:д\.|ул\.|кв\.|корп|обл\b|обл\.|г\.|пр-?д|пр-?т|ш\.|пер\.)",
    re.IGNORECASE,
)
_RE_KEYWORDS = re.compile(
    r"(договор|управл|площад|подпис|отч[её]т|МКД|кв\.?\s*м|"
    r"ОГРН|ИНН|КПП|ФИО|адрес|стоимост|тариф|услуг|работ|общее\s+имуществ)",
    re.IGNORECASE,
)
_JUNK_ONLY = set("|~`^=<>{}[]()/\\!@#$%&*_+-—–«»\"'.,:;…°·•")

def _junk_ratio(s: str) -> float:
    if not s:
        return 1.0
    total = 0
    junk = 0
    for ch in s:
        if ch.isspace():
            continue
        total += 1
        if ch in _JUNK_ONLY:
            junk += 1
    return junk / total if total else 1.0

def _cyr_ratio(s: str) -> float:
    cyr = len(_RE_CYR.findall(s))
    lat = len(_RE_LAT.findall(s))
    total = cyr + lat
    return cyr / total if total else 0.0

def _strip_junk_edges(s: str) -> str:
    return s.strip(" \t" + "".join(_JUNK_ONLY))

def clean_ocr_text(raw: str) -> str:
    if not raw:
        return ""
    out_lines = []
    prev_line = None
    for line in raw.splitlines():
        s = _strip_junk_edges(line)
        if not s or s == prev_line:
            continue
        prev_line = s

        if _RE_KEYWORDS.search(s):
            out_lines.append(s); continue
        if _RE_DATE.search(s):
            out_lines.append(s); continue
        if _RE_ADDR_KEY.search(s):
            out_lines.append(s); continue

        significant = [c for c in s if not c.isspace() and c not in _JUNK_ONLY]
        if len(significant) < 3:
            continue
        if _junk_ratio(s) > 0.4:
            continue

        cyr_count = len(_RE_CYR.findall(s))
        if cyr_count >= 5 and _cyr_ratio(s) >= 0.4:
            out_lines.append(s); continue

        letters = len(_RE_CYR.findall(s)) + len(_RE_LAT.findall(s))
        if len(s) >= 25 and letters >= 10:
            out_lines.append(s); continue
    return "\n".join(out_lines).strip()

# ============================== SNAPSHOT ==============================
def read_visible_text(driver) -> str:
    try:
        txt = driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        )
        return (txt or "").strip()
    except Exception:
        return ""

def snapshot(driver, tag: str, file_stem: str,
             extra: str = "", with_visible_text: bool = True,
             override_description: str = None):
    try:
        n = _scan_max_seq() + 1
        scr_name = f"scr{n:06d}.png"
        out_path = IMG_DIR / scr_name

        tmp_png = IMG_DIR / f"_tmp_{n:06d}.png"
        ok = driver.save_screenshot(str(tmp_png))
        if not ok:
            print("[!] save_screenshot returned False")
            return None

        try:
            img = Image.open(tmp_png).convert("RGB")
            img_q = img.quantize(colors=256, method=Image.MEDIANCUT)

            if override_description is not None:
                desc = override_description
            elif with_visible_text:
                desc = read_visible_text(driver)
            else:
                desc = ""

            cur_url = ""
            try:
                cur_url = driver.current_url or ""
                if cur_url.startswith("data:"):
                    cur_url = "http://localhost/view.htm"
                elif len(cur_url) > 900:
                    cur_url = cur_url[:900] + "..."
            except Exception:
                pass

            meta = _build_itxt_meta(
                title=f"{file_stem} / {tag}",
                comment=extra or "",
                description=desc,
                extra_source=cur_url,
            )
            img_q.save(str(out_path), "PNG",
                       bits=PNG_BITS, pnginfo=meta)
            img.close()
            try:
                img_q.close()
            except Exception:
                pass
        finally:
            try:
                if tmp_png.exists():
                    tmp_png.unlink()
            except Exception:
                pass

        _register_seq(scr_name, file_stem, tag)
        log(f"    [snap] {scr_name} ({file_stem}_{tag})")
        return scr_name
    except Exception as e:
        print(f"[!] snapshot failed: {e}")
        return None

def _scroll_banner_away(driver):
    """
    Прокручивает страницу так, чтобы жёлтый баннер о регламентных
    работах ушёл за верх вьюпорта. Баннер НЕ удаляется из DOM —
    в iTXt-чанке Description его текст сохраняется.
    Если баннер не найден — ничего не делаем.
    """
    try:
        scrolled = driver.execute_script("""
            var texts = ['регламентных работ', 'ограничение доступности',
                         'Внимание! В связи с проведением'];
            var all = document.querySelectorAll('div, section, aside');
            for (var i = 0; i < all.length; i++) {
                var t = all[i].innerText || '';
                for (var j = 0; j < texts.length; j++) {
                    if (t.indexOf(texts[j]) >= 0 && t.length < 500) {
                        var r = all[i].getBoundingClientRect();
                        var y = r.bottom + window.scrollY;
                        window.scrollTo(0, y);
                        return y;
                    }
                }
            }
            return -1;
        """)
        if scrolled is not None and scrolled >= 0:
            log(f"    [scroll] баннер уведён за вьюпорт (y={scrolled})")
            time.sleep(0.6)
        else:
            log("    [scroll] баннер не найден — оставляем как есть")
    except Exception as e:
        log(f"    [scroll] ошибка: {type(e).__name__}: {e}")

# ============================== РЕНДЕР PDF ==============================
def _render_one_pdf_page(pdf_path: Path, page_idx: int,
                         out_png: Path) -> bool:
    try:
        doc = fitz.open(str(pdf_path))
        if page_idx < 0 or page_idx >= doc.page_count:
            doc.close()
            return False
        page = doc.load_page(page_idx)
        zoom = PDF_DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(out_png))
        doc.close()
        return True
    except Exception as e:
        log(f"    [pdf] рендер страницы idx={page_idx} упал: "
            f"{type(e).__name__}: {e}")
        return False

def _paste_page_into_cell(canvas, img, cell_x, cell_y, cell_w, cell_h):
    w, h = img.size
    if w <= 0 or h <= 0:
        return
    scale = min(cell_w / w, cell_h / h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    img_r = img.resize((new_w, new_h), Image.LANCZOS)
    off_x = cell_x + (cell_w - new_w) // 2
    off_y = cell_y + (cell_h - new_h) // 2
    canvas.paste(img_r, (off_x, off_y))
    try:
        img_r.close()
    except Exception:
        pass

def _render_stub_png(png_path: Path, text: str) -> bool:
    try:
        img = Image.new("RGB", (PDF_W, PDF_H), (255, 255, 255))
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)

        max_w = PDF_W - 2 * STUB_MARGIN_X
        max_h = PDF_H - 2 * STUB_MARGIN_Y
        size = STUB_MAX_FONT_SIZE
        while size >= STUB_MIN_FONT_SIZE:
            try:
                font = ImageFont.truetype(STUB_FONT_PATH, size)
            except Exception:
                font = ImageFont.load_default()
                size = 12
                break
            bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=0)
            w = bbox[2] - bbox[0]
            lines = text.count("\n") + 1
            h_total = int(lines * size * STUB_LINE_SPACING)
            if w <= max_w and h_total <= max_h:
                break
            size -= 1

        try:
            font = ImageFont.truetype(STUB_FONT_PATH, size)
        except Exception:
            font = ImageFont.load_default()
            size = 12

        y = STUB_MARGIN_Y
        for line in text.split("\n"):
            draw.text((STUB_MARGIN_X, y), line, font=font, fill=(0, 0, 0))
            y += int(size * STUB_LINE_SPACING)

        img_q = img.quantize(colors=256, method=Image.MEDIANCUT)
        img_q.save(png_path, "PNG", bits=PNG_BITS)
        img.close()
        try:
            img_q.close()
        except Exception:
            pass
        log(f"    [stub] отрисована заглушка: {png_path.name} "
            f"(кегль={size}, {png_path.stat().st_size} байт)")
        return True
    except Exception as e:
        log(f"    [stub] ошибка отрисовки: {type(e).__name__}: {e}")
        return False

def _build_pdf_view_data_url(png_path: Path) -> str:
    with open(png_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    html = (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>view</title><style>"
        "html,body{margin:0;padding:0;background:#fff;overflow:hidden;}"
        f"img{{width:{PDF_W}px;height:{PDF_H}px;display:block;}}"
        "</style></head><body>"
        f"<img src='data:image/png;base64,{b64}' alt='scan'>"
        "</body></html>"
    )
    return "data:text/html;charset=utf-8," + urllib.parse.quote(html)

_PDF_TS_JS = r"""
(function() {
    var OVERRIDE_URL = 'http://localhost/view.htm';
    var hostId = '__ts_host__';
    var host = document.getElementById(hostId);
    if (!host) {
        host = document.createElement('div');
        host.id = hostId;
        host.style.cssText = [
            'all: initial !important','position: fixed !important',
            'right: 10px !important','bottom: 10px !important',
            'width: 0 !important','height: 0 !important',
            'z-index: 2147483647 !important','pointer-events: none !important',
            'margin: 0 !important','padding: 0 !important','border: 0 !important'
        ].join('; ');
        document.documentElement.appendChild(host);
        var shadow = host.attachShadow({ mode: 'open' });
        var style = document.createElement('style');
        style.textContent = `
            .ts-box {
                position: fixed; right: 10px; bottom: 10px;
                background: rgba(0,0,0,0.82); color: #00ff00;
                font: 14px/1.3 monospace; padding: 6px 10px;
                border-radius: 6px; white-space: pre; text-align: left;
                box-shadow: 0 0 6px rgba(0,255,0,0.6);
                pointer-events: none; z-index: 2147483647;
                max-width: 90vw; width: auto; height: auto;
            }`;
        shadow.appendChild(style);
        var box = document.createElement('div');
        box.className = 'ts-box'; box.id = '__ts_box__';
        shadow.appendChild(box);
    }
    var shadowRoot = host.shadowRoot;
    var box = shadowRoot.getElementById('__ts_box__');
    if (!box) return;
    function fmt(d) {
        function p(n){ return n<10 ? '0'+n : ''+n; }
        return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '
             + p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
    }
    function tick() {
        var d = new Date();
        var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        box.textContent = fmt(d) + '  ' + tz + '\n' + OVERRIDE_URL;
    }
    tick();
    if (!window.__ts_interval__) window.__ts_interval__ = setInterval(tick, 1000);
})();
"""

def _snapshot_png_in_tab(driver, png_path: Path, file_stem: str,
                         tag: str, extra: str = "",
                         override_description: str = None):
    try:
        data_url = _build_pdf_view_data_url(png_path)
        driver.get(data_url)

        waited = 0.0
        while waited < 15.0:
            try:
                cur = driver.current_url or ""
            except Exception:
                cur = ""
            if cur.startswith("data:"):
                break
            time.sleep(0.5)
            waited += 0.5

        waited_img = 0.0
        while waited_img < 10.0:
            try:
                n = driver.execute_script(
                    "return document.querySelectorAll('img').length;"
                )
            except Exception:
                n = 0
            if n and n > 0:
                break
            time.sleep(0.5)
            waited_img += 0.5

        time.sleep(2.0)
        try:
            driver.execute_script(_PDF_TS_JS)
        except Exception as e:
            log(f"    [pdf-snap] overlay: {e}")
        time.sleep(1.5)

        scr = snapshot(driver, tag, file_stem, extra,
                       with_visible_text=False,
                       override_description=override_description)
        return scr
    except Exception as e:
        log(f"    [pdf-snap] упал: {type(e).__name__}: {e}")
        return None

def _ocr_png(png_path: Path) -> str:
    try:
        img = Image.open(png_path).convert("RGB")
        txt = pytesseract.image_to_string(img, lang=OCR_LANG, config="--psm 6")
        img.close()
        return clean_ocr_text((txt or "").strip())
    except pytesseract.TesseractNotFoundError:
        log("    [ocr] Tesseract не найден")
        return ""
    except Exception as e:
        log(f"    [ocr] ошибка: {type(e).__name__}: {e}")
        return ""

def _has_ufo_text(png_path: Path) -> bool:
    try:
        img = Image.open(png_path)
        info = dict(img.info or {})
        desc = info.get("Description", "") or ""
        img.close()
        return "квалифицированной электронной подписи" in desc.lower()
    except Exception:
        return False

def _do_pdf_collage_scrn(driver, pdf_path: Path, file_stem: str,
                         file_size: int, extra: str = ""):
    if not pdf_path.exists():
        log("    [collage] PDF не существует — пропуск")
        return None

    if 0 < file_size < PDF_STUB_THRESHOLD:
        log(f"    [collage] файл {file_size} байт < "
            f"{PDF_STUB_THRESHOLD} — HTML-заглушка")
        try:
            text = pdf_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            text = f"[не удалось прочитать {pdf_path.name}]\n\n{e}"
        tmp_png = IN_DIR / f"_stub_{file_stem}.png"
        if not _render_stub_png(tmp_png, text):
            return None
        scr = _snapshot_png_in_tab(driver, tmp_png, file_stem,
                                   "04_pdf_collage", extra,
                                   override_description=text)
        try:
            tmp_png.unlink()
        except Exception:
            pass
        return scr

    try:
        doc = fitz.open(str(pdf_path))
        n_pages = doc.page_count
        doc.close()
    except Exception as e:
        log(f"    [collage] не открыть PDF: {type(e).__name__}: {e}")
        return None

    if n_pages == 0:
        log("    [collage] PDF пуст")
        return None

    if n_pages <= 4:
        idx_list = list(range(n_pages))
    else:
        idx_list = [0, 1, n_pages - 2, n_pages - 1]
    log(f"    [collage] страниц в PDF: {n_pages}, берём idx={idx_list}")

    tmp_pages = []
    ocr_parts = []
    for out_i, src_idx in enumerate(idx_list):
        tmp_png = IN_DIR / f"_pdf_page_{file_stem}_{out_i}.png"
        if not _render_one_pdf_page(pdf_path, src_idx, tmp_png):
            continue
        tmp_pages.append(tmp_png)

        try:
            img = Image.open(tmp_png).convert("RGB")
            raw_ocr = pytesseract.image_to_string(
                img, lang=OCR_LANG, config="--psm 6"
            )
            img.close()
            cleaned = clean_ocr_text((raw_ocr or "").strip())
            ocr_parts.append(f"--- стр. {src_idx + 1} ---\n{cleaned}")
            log(f"    [collage] стр.{src_idx}: OCR {len(cleaned)} симв.")
        except pytesseract.TesseractNotFoundError:
            log("    [collage] Tesseract не найден")
            ocr_parts.append(f"--- стр. {src_idx + 1} ---\n")
        except Exception as e:
            log(f"    [collage] OCR стр.{src_idx} упал: "
                f"{type(e).__name__}: {e}")
            ocr_parts.append(f"--- стр. {src_idx + 1} ---\n")

    if not tmp_pages:
        log("    [collage] ни одна страница не отрендерилась — стоп")
        return None

    canvas = Image.new("RGB", (PDF_W, PDF_H), (255, 255, 255))
    cols = 4
    cell_w = PDF_W // cols
    cell_h = PDF_H
    for i, p in enumerate(tmp_pages):
        if i >= cols:
            break
        cell_x = i * cell_w
        cell_y = 0
        try:
            img = Image.open(p).convert("RGB")
        except Exception:
            continue
        _paste_page_into_cell(canvas, img, cell_x, cell_y, cell_w, cell_h)
        img.close()

    collage_png = IN_DIR / f"_collage_{file_stem}.png"
    img_q = canvas.quantize(colors=256, method=Image.MEDIANCUT)
    img_q.save(str(collage_png), "PNG", bits=PNG_BITS)
    canvas.close()
    try:
        img_q.close()
    except Exception:
        pass

    for p in tmp_pages:
        try:
            p.unlink()
        except Exception:
            pass

    log(f"    [collage] собран: {collage_png.name} "
        f"({collage_png.stat().st_size} байт)")

    full_ocr = "\n\n".join(ocr_parts)
    scr = _snapshot_png_in_tab(driver, collage_png, file_stem,
                               "04_pdf_collage", extra,
                               override_description=full_ocr)

    try:
        collage_png.unlink()
    except Exception:
        pass

    return scr

# ============================== ОВЕРЛЕЙ ==============================
_TS_JS = r"""
(function() {
    var hostId = '__ts_host__';
    var host = document.getElementById(hostId);
    if (!host) {
        host = document.createElement('div');
        host.id = hostId;
        host.style.cssText = [
            'all: initial !important','position: fixed !important',
            'right: 10px !important','bottom: 10px !important',
            'width: 0 !important','height: 0 !important',
            'z-index: 2147483647 !important','pointer-events: none !important',
            'margin: 0 !important','padding: 0 !important','border: 0 !important'
        ].join('; ');
        document.documentElement.appendChild(host);
        var shadow = host.attachShadow({ mode: 'open' });
        var style = document.createElement('style');
        style.textContent = `
            .ts-box {
                position: fixed; right: 10px; bottom: 10px;
                background: rgba(0,0,0,0.82); color: #00ff00;
                font: 14px/1.3 monospace; padding: 6px 10px;
                border-radius: 6px; white-space: pre; text-align: left;
                box-shadow: 0 0 6px rgba(0,255,0,0.6);
                pointer-events: none; z-index: 2147483647;
                max-width: 90vw; width: auto; height: auto;
            }`;
        shadow.appendChild(style);
        var box = document.createElement('div');
        box.className = 'ts-box'; box.id = '__ts_box__';
        shadow.appendChild(box);
    }
    var shadowRoot = host.shadowRoot;
    var box = shadowRoot.getElementById('__ts_box__');
    if (!box) return;
    function fmt(d) {
        function p(n){ return n<10 ? '0'+n : ''+n; }
        return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '
             + p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
    }
    function tick() {
        var d = new Date();
        var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        box.textContent = fmt(d) + '  ' + tz + '\n' + location.href;
    }
    tick();
    if (!window.__ts_interval__) window.__ts_interval__ = setInterval(tick, 1000);
})();
"""

def inject_timestamp_overlay(driver):
    try:
        driver.execute_script(_TS_JS)
    except Exception as e:
        print("[!] overlay:", e)

def set_ufo_zoom(driver):
    try:
        driver.execute_script("document.body.style.zoom='65%';")
    except Exception:
        pass

# ============================== ВСПОМОГАТЕЛЬНОЕ ==============================
def read_ogrns(path: Path) -> list:
    if not path.exists():
        raise FileNotFoundError(f"no {path}")
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or not re.fullmatch(r"\d{13}", s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def read_selected_text(driver) -> str:
    for sel in ("span.select2-chosen",
                ".select2-selection__rendered",
                "span.select2-selection__rendered"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return (els[0].text or "").strip()
        except Exception:
            pass
    return ""

def read_input_value(driver) -> str:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "input.select2-input")
        if els:
            return els[0].get_attribute("value") or ""
    except Exception:
        pass
    return ""

def drop_select2_mask(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll('.select2-drop-mask').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop-active').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop').forEach(e => e.remove());
        """)
    except Exception:
        pass

def click_diagonal_cross(driver) -> str:
    for sel in [
        "a.select2-search-choice-close",
        "abbr.select2-search-choice-close",
        ".select2-search-choice-close",
        ".select2-selection__clear",
    ]:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                continue
            el = els[0]
            if not el.is_displayed():
                continue
            el.click()
            time.sleep(0.6)
            return f"clicked:{sel}"
        except Exception as e:
            return f"error:{sel}:{e}"
    return "not_found"

def clean_dir_files(directory: Path):
    if not directory.exists():
        return
    for item in directory.iterdir():
        if item.is_file():
            try:
                item.unlink()
            except Exception:
                pass
        elif item.is_dir():
            try:
                shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass

def wait_file(directory: Path, pattern: str, timeout: int = 30):
    end = time.time() + timeout
    last_size = -1
    while time.time() < end:
        candidates = [f for f in directory.iterdir()
                      if re.search(pattern, f.name)]
        if candidates:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            size = newest.stat().st_size
            if size > 0 and size == last_size:
                return newest
            last_size = size
        time.sleep(1)
    return None

def extract_zip(zip_path: Path, out_dir: Path) -> bool:
    if zip_path is None or not zip_path.exists():
        return False
    out_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        time.sleep(1)
        result = subprocess.run(
            [WINRAR, "x", "-y", str(zip_path), str(out_dir) + os.sep],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)
    return False

# ============================== ПАРЫ PDF+P7S ==============================
def _iter_all_files(directory: Path):
    if not directory.exists():
        return
    for f in directory.rglob("*"):
        if f.is_file():
            yield f

def find_pdf_p7s_pair_single(directory: Path):
    pdfs = [f for f in _iter_all_files(directory)
            if f.suffix.lower() == ".pdf"]
    p7s_list = [f for f in _iter_all_files(directory)
                if f.suffix.lower() in (".p7s", ".sig")]

    if len(pdfs) != 1 or len(p7s_list) != 1:
        return []

    pdf = pdfs[0]
    p7s = p7s_list[0]
    p7s_stem = p7s.stem

    if EP_PREFIX_RE.match(p7s_stem):
        clean = EP_PREFIX_RE.sub("", p7s_stem)
        clean_base = re.sub(r"\.pdf$", "", clean, flags=re.IGNORECASE)
        if clean_base == pdf.stem:
            return [(pdf, p7s)]
        log(f"    [pair-single] strict mismatch")
        return []
    else:
        if pdf.name in p7s.name:
            log(f"    [pair-single] soft match: {pdf.name!r}")
            return [(pdf, p7s)]
        return []

def find_pdf_p7s_pair_multi(directory: Path, actual_pdf_name: str):
    target_norm = _norm_name(actual_pdf_name).lower()
    pdf_path = None
    p7s_found = []

    try:
        for f in _iter_all_files(directory):
            if _norm_name(f.name).lower() == target_norm:
                pdf_path = f
                continue
            lowname = f.name.lower()
            if lowname.endswith(".p7s") or lowname.endswith(".sig"):
                for ext in (".p7s", ".sig"):
                    want = _norm_name(f"{actual_pdf_name}{ext}").lower()
                    if _norm_name(f.name).lower() == want:
                        p7s_found.append(f)
                        break
    except Exception as e:
        log(f"    [pair-multi] обход архива упал: {e}")
        return [], None

    if pdf_path is None:
        return [], None
    if len(p7s_found) == 0:
        return [(pdf_path, None)], pdf_path
    if len(p7s_found) > 1:
        return [], pdf_path
    return [(pdf_path, p7s_found[0])], pdf_path

def move_to_old(files):
    for f in files:
        if f is None or not f.exists():
            continue
        target = OLD_DIR / f.name
        for _ in range(15):
            try:
                if target.exists():
                    target.unlink()
                shutil.move(str(f), str(target))
                break
            except PermissionError:
                time.sleep(1)

def close_tabs_except(driver, keep_handles):
    if not hasattr(driver, "window_handles"):
        return
    for h in list(driver.window_handles):
        if h not in keep_handles:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass

def open_new_tab_and_switch(driver, wait, click_fn):
    handles_before = set(driver.window_handles)
    click_fn()
    wait.until(lambda d: len(set(d.window_handles) - handles_before) > 0)
    new_handle = (set(driver.window_handles) - handles_before).pop()
    driver.switch_to.window(new_handle)
    time.sleep(2)
    inject_timestamp_overlay(driver)
    return new_handle

# ============================== ПОСЛЕДНЯЯ НАДЕЖДА ==============================
def _fb_get_text(el) -> str:
    try:
        t = el.get_attribute("textContent")
        if t:
            return t
    except Exception:
        pass
    try:
        return (el.text or "")
    except Exception:
        return ""

def _fb_click_hoverable(driver, el, label: str) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", el
        )
        time.sleep(0.3)
    except Exception:
        pass

    try:
        ActionChains(driver).move_to_element(el).pause(0.4).click(el).perform()
        log(f"    [fb] {label}: ActionChains-клик выполнен")
        return True
    except WebDriverException as e:
        log(f"    [fb] {label}: ActionChains-клик упал: {e}")

    try:
        driver.execute_script("arguments[0].click();", el)
        log(f"    [fb] {label}: JS-клик выполнен")
        return True
    except Exception as e:
        log(f"    [fb] {label}: JS-клик упал: {e}")
        return False

def _return_to_mgmt_tab(driver, mgmt_handle):
    try:
        close_tabs_except(driver, [ufo_handle, gis_handle, mgmt_handle])
    except Exception as e:
        log(f"    [fallback] close_tabs_except упал: {e}")
    try:
        driver.switch_to.window(mgmt_handle)
        time.sleep(1)
        inject_timestamp_overlay(driver)
        log("    [fallback] вернулись на 'Информация об управлении МКД'")
        return True
    except Exception as e:
        log(f"    [fallback] не удалось переключиться на mgmt: {e}")
        return False

def download_files_individually(driver, wait,
                                target_pdf_name: str) -> list:
    log("    [fb] === пофайловое скачивание ===")
    target_norm = _norm_name(target_pdf_name)
    log(f"    [fb] target_pdf_name={target_pdf_name!r} "
        f"→ norm={target_norm!r}")
    time.sleep(FB_WAIT_RENDER)

    raw_names = driver.find_elements(
        By.CSS_SELECTOR,
        "div.file-panel__row-item.file-panel__name span.ng-binding"
    )
    name_spans = []
    for el in raw_names:
        t = _fb_get_text(el)
        if not t or not t.strip().lower().endswith(".pdf") or "(" in t:
            continue
        name_spans.append((el, t))

    log(f"    [fb] PDF на странице: {len(name_spans)}")
    for el, t in name_spans:
        log(f"        raw={t!r} → norm={_norm_name(t)!r}")

    relevant = []
    for el, t in name_spans:
        if _norm_name(t) == target_norm:
            relevant.append((el, t))

    if not relevant:
        log(f"    [fb] PDF {target_pdf_name!r} не найден среди имён")
        return None
    if len(relevant) > 1:
        log(f"    [fb] найдено {len(relevant)} PDF с именем "
            f"{target_pdf_name!r} — стоп")
        return None

    target_name_el = relevant[0][0]
    log(f"    [fb] ЦЕЛЬ: {relevant[0][1]!r}")

    sig_icon = None
    for xp in (
        "preceding::span[contains(@class, 'gis-icon-signature')][1]",
        "preceding::span[contains(@class, 'app-icon_cl_prime')][1]",
        "preceding::span[contains(@class, 'app-icon_cl_asphalt')][1]",
        "preceding::span[contains(@class, 'signature')][1]",
    ):
        try:
            sig_icon = target_name_el.find_element(By.XPATH, xp)
            if sig_icon:
                log(f"    [fb] sig-icon найден через {xp!r}")
                break
        except Exception:
            sig_icon = None

    if sig_icon is None:
        log("    [fb] иконка ЭП не найдена — стоп")
        return None

    log("    [fb] --- клик по ЭП-иконке ---")
    if not _fb_click_hoverable(driver, sig_icon, "sig"):
        return None
    time.sleep(FB_WAIT_AFTER_CLICK)

    log("    [fb] --- клик по ИМЕНИ PDF-файла ---")
    try:
        pdf_clickable = target_name_el.find_element(
            By.XPATH,
            "./ancestor::span[contains(@class, 'soh__trigger')][1]"
        )
    except Exception as e:
        log(f"    [fb] родитель soh__trigger не найден, клик по name: {e}")
        pdf_clickable = target_name_el

    if not _fb_click_hoverable(driver, pdf_clickable, "pdf_name"):
        return None

    end = time.time() + 30
    pdf_files = []
    p7s_files = []
    while time.time() < end:
        try:
            pdf_files = [f for f in DOWNLOADS.iterdir()
                         if f.is_file() and f.suffix.lower() == ".pdf"]
            p7s_files = [f for f in DOWNLOADS.iterdir()
                         if f.is_file() and f.suffix.lower() in (".p7s", ".sig")]
        except Exception:
            pdf_files = []
            p7s_files = []
        if len(pdf_files) >= 1 and len(p7s_files) >= 1:
            break
        time.sleep(1)

    log(f"    [fb] в Downloads: pdf={len(pdf_files)} p7s={len(p7s_files)}")
    for f in pdf_files + p7s_files:
        try:
            log(f"        {f.name!r}  size={f.stat().st_size}")
        except Exception:
            pass

    if len(pdf_files) != 1 or len(p7s_files) != 1:
        log("    [fb] не 1 пара PDF+P7S")
        return None

    return [pdf_files[0], p7s_files[0]]

# ============================== ГИС ЖКХ ==============================
def open_uk_tab(driver, wait: WebDriverWait):
    time.sleep(3)
    tab = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(., 'Поиск дома по управляющей организации')]")
    ))
    tab.click()
    time.sleep(2)

def select_org(driver, wait: WebDriverWait, ogrn: str) -> bool:
    drop_select2_mask(driver)
    time.sleep(0.3)

    choice = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "span.select2-choice, span.select2-chosen")
    ))
    try:
        choice.click()
    except Exception:
        driver.execute_script("arguments[0].click();", choice)
    time.sleep(0.5)

    search_input = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input.select2-input")
    ))
    driver.execute_script("arguments[0].focus();", search_input)
    search_input.clear()
    time.sleep(0.3)
    search_input.send_keys(ogrn)
    time.sleep(1.5)

    try:
        result_link = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR,
             "ul.select2-results li.select2-result-selectable a, "
             "ul.select2-results li.select2-result-selectable")
        ))
    except TimeoutException:
        log(f"    OGRN {ogrn} not found")
        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass
        return False

    result_link.click()
    time.sleep(2)

    find_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[normalize-space(.)='Найти']")
    ))
    find_btn.click()
    time.sleep(2)

    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located(
            (By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]")
        ))
        return True
    except TimeoutException:
        log(f"    no house cards for {ogrn}")
        return False

def read_total_records(driver) -> int:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = TOTAL_RECORDS_RE.search(body)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.XPATH,
            "//*[contains(normalize-space(.), 'Всего записей')]"
            "//b[contains(@class, 'ng-binding')]"
        )
        for el in els:
            txt = (el.text or "").strip()
            m = re.search(r"(\d+)", txt)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return -1

def set_page_size_100(driver, wait: WebDriverWait) -> bool:
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "select#count")
    except Exception:
        candidates = []

    if not candidates:
        log("    select#count отсутствует — пагинация не нужна")
        return True

    select_el = candidates[0]
    try:
        if not select_el.is_displayed() or not select_el.is_enabled():
            return True
    except Exception:
        pass

    for attempt in range(1, 4):
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", select_el
            )
            time.sleep(0.5)
            Select(select_el).select_by_value("100")
            time.sleep(2)
            log(f"    page size 100 set (attempt {attempt})")
            return True
        except Exception as e:
            log(f"    set_page_size_100 attempt {attempt} failed: {e}")
            time.sleep(1.5)

    return True

def read_contract_date(driver):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = re.search(
            r"Дата заключения договора управления[^\d]{0,80}(\d{2}\.\d{2}\.\d{4})",
            body
        )
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "span.form-base__form-value")
        for el in els:
            txt = (el.text or "").strip()
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", txt)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"

# ============================== ПЛОЩАДИ ==============================
def _read_square_by_ng(driver, ng_bind_fragment: str) -> str:
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            f"td[ng-bind-html*='{ng_bind_fragment}']"
        )
        if not els:
            return ""
        raw = (els[0].text or "").strip()
        m = re.search(r"(\d+(?:[.,]\d+)?)", raw)
        if not m:
            return ""
        return m.group(1).replace(",", ".")
    except Exception:
        return ""

def _read_4_squares_by_labels(driver) -> dict:
    result = {"p_total": "", "p_res": "", "p_nres": "", "p_common": ""}
    labels = [
        ("p_total", r"Общая\s+площадь\s+дома,?\s*кв\.?\s*м"),
        ("p_res", r"Общая\s+площадь\s+жилых\s+помещений,?\s*кв\.?\s*м"),
        ("p_nres", r"Общая\s+площадь\s+нежилых\s+помещений,?\s*кв\.?\s*м"),
        ("p_common",
         r"Общая\s+площадь\s+помещений,?\s+входящих\s+в\s+состав\s+"
         r"общего\s+имущества,?\s*кв\.?\s*м"),
    ]
    try:
        tds = driver.find_elements(By.CSS_SELECTOR, "td")
        for key, rx in labels:
            rxc = re.compile(rx, re.IGNORECASE)
            for i, td in enumerate(tds):
                txt = (td.text or "").strip()
                if rxc.search(txt) and i + 1 < len(tds):
                    next_txt = (tds[i + 1].text or "").strip()
                    m = re.search(r"(-?\d+(?:[.,]\d+)?)", next_txt)
                    if m:
                        result[key] = m.group(1).replace(",", ".")
                        break
    except Exception as e:
        log(f"    [squares-labels] fallback упал: {e}")
    return result

def _read_4_squares_positional(driver) -> dict:
    result = {"p_total": "", "p_res": "", "p_nres": "", "p_common": ""}
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-base__form-value._padding-0._top.ng-binding"
        )
        texts = []
        for el in els:
            t = (el.text or "").strip()
            m = re.search(r"(-?\d+(?:[.,]\d+)?)", t)
            texts.append(m.group(1).replace(",", ".") if m else "")
        if len(texts) >= 4:
            result["p_total"] = texts[0]
            result["p_res"] = texts[1]
            result["p_nres"] = texts[2]
            result["p_common"] = texts[3]
    except Exception as e:
        log(f"    [squares-pos] упал: {e}")
    return result

def _read_4_squares_mgmt(driver) -> dict:
    result = _read_4_squares_by_labels(driver)
    log(f"    [squares-mgmt-labels] total={result['p_total']!r} "
        f"res={result['p_res']!r} nres={result['p_nres']!r} "
        f"common={result['p_common']!r}")

    if not all(result.values()):
        pos = _read_4_squares_positional(driver)
        log(f"    [squares-mgmt-pos] total={pos['p_total']!r} "
            f"res={pos['p_res']!r} nres={pos['p_nres']!r} "
            f"common={pos['p_common']!r}")
        for k, v in pos.items():
            if not result.get(k) and v:
                result[k] = v

    return result

def read_house_squares_card(driver) -> tuple:
    p_total = _read_square_by_ng(driver, "totalSquare")
    p_res = _read_square_by_ng(driver, "residentialSquare")
    return p_total, p_res

def read_page_addr_card(driver) -> str:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "h1.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            txt = re.sub(r"\s*На\s*карте\s*$", "", txt,
                         flags=re.IGNORECASE).strip()
            if re.search(r"\d{6},", txt):
                return txt
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-base__form-value.ng-binding[ng-bind='addressInfo.formattedAddress']"
        )
        for el in els:
            txt = (el.text or "").strip()
            if txt:
                return txt
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, "span.form-base__form-value.ng-binding"
        )
        for el in els:
            txt = (el.text or "").strip()
            if re.search(r"\d{6},", txt):
                return txt
    except Exception:
        pass
    return ""

def read_page_org_card(driver) -> str:
    try:
        els = driver.find_elements(
            By.XPATH,
            "//td[contains(normalize-space(.), 'Управляющая организация')]"
            "/following-sibling::td[1]//a[contains(@class, 'ng-binding')]"
        )
        for el in els:
            txt = (el.text or "").strip()
            if txt and len(txt) > 3:
                return _normalize_org(txt)
    except Exception:
        pass

    try:
        els = driver.find_elements(By.CSS_SELECTOR, "a.ctrl-link.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if txt and len(txt) > 5:
                return _normalize_org(txt)
    except Exception:
        pass

    try:
        els = driver.find_elements(By.CSS_SELECTOR, "a.cnt-link.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if txt and len(txt) > 5:
                return _normalize_org(txt)
    except Exception:
        pass
    return ""

# ============================== ЭП / ДОГОВОР ==============================
def classify_ep(cls: str, color: str) -> str:
    cls_low = (cls or "").lower()
    color_low = (color or "").lower().replace(" ", "")
    if "app-icon_cl_prime" in cls_low:
        return "present"
    if "app-icon_cl_asphalt" in cls_low:
        return "absent"
    if color_low.startswith("rgb(13,126,214"):
        return "present"
    if color_low.startswith("rgb(136,136,136"):
        return "absent"
    return "unknown"

def scan_files_with_ep(driver) -> list:
    rows_out = []
    try:
        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "div.file-panel__row.file-panel__justify-start"
        )
    except Exception:
        return rows_out

    for row in rows:
        try:
            if not row.find_elements(By.CSS_SELECTOR, "span.icon-file_pdf"):
                continue
        except Exception:
            continue

        name_raw = ""
        try:
            cands = row.find_elements(
                By.CSS_SELECTOR,
                ".file-panel__row-item.file-panel__name span.ng-binding"
            )
            for el in cands:
                try:
                    style = (el.get_attribute("style") or "").lower()
                    text = _fb_get_text(el)
                except Exception:
                    continue
                if "0075c0" in style and text:
                    name_raw = text
                    break
            if not name_raw:
                for el in cands:
                    try:
                        text = _fb_get_text(el)
                    except Exception:
                        text = ""
                    if text and text.strip() != "Документ PDF":
                        name_raw = text
                        break
        except Exception:
            pass

        name = _norm_name(name_raw)

        real_name = ""
        try:
            tooltips = row.find_elements(
                By.CSS_SELECTOR,
                "span.soh__content.common-tooltip__content.ng-binding"
            )
            for t in tooltips:
                try:
                    txt = (t.get_attribute("textContent") or "").strip()
                except Exception:
                    txt = ""
                m = _RE_REALNAME.match(txt)
                if m:
                    real_name = _norm_name(m.group(1))
                    break
        except Exception:
            pass

        ep_cls, ep_color = "", ""
        try:
            sig_els = row.find_elements(By.CSS_SELECTOR,
                                        "span.gis-icon-signature")
            if sig_els:
                sig = sig_els[0]
                ep_cls = sig.get_attribute("class") or ""
                try:
                    ep_color = driver.execute_script(
                        "return getComputedStyle(arguments[0]).color;", sig
                    ) or ""
                except Exception:
                    ep_color = ""
        except Exception:
            pass

        ep_state = classify_ep(ep_cls, ep_color)
        is_pdf = bool(name) and bool(PDF_EXT_RE.search(name))
        is_contract = (
            is_pdf
            and bool(CONTRACT_RE.search(name))
            and not bool(ADDENDUM_RE.search(name))
        )

        rows_out.append({
            "name": name,
            "real_name": real_name,
            "ep_state": ep_state,
            "is_pdf": is_pdf,
            "is_contract": is_contract,
        })

    return rows_out

def pick_contract_pdf(rows: list):
    pdf_rows = [r for r in rows if r["is_pdf"]]
    if not pdf_rows:
        return None

    cands = [r for r in pdf_rows if r["is_contract"]]

    if not cands:
        cands = [
            r for r in pdf_rows
            if not ADDENDUM_RE.search(r["name"] or "")
        ]
        if len(cands) != 1:
            cands = []

    if not cands:
        if len(pdf_rows) == 1:
            cands = pdf_rows
        else:
            return None

    def _pick(r):
        return {
            "name": r["name"],
            "real_name": r.get("real_name") or "",
            "ep_state": r["ep_state"],
        }

    if len(cands) == 1:
        return _pick(cands[0])

    presents = [r for r in cands if r["ep_state"] == "present"]
    if presents:
        return _pick(presents[0])

    return _pick(cands[0])

# ============================== СБОР ПОЛЕЙ С ГИС ЖКХ ==============================
def count_pdf_icons(driver) -> int:
    try:
        return len(driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-upload__icon.icon-file.icon-file_pdf"
        ))
    except Exception:
        return -1

def collect_house_info(driver, p_total: str = "", p_residential: str = ""):
    info = {"org": "", "addr": "", "pdf_name": "", "real_pdf_name": "",
            "date": "", "pdf_icons": -1, "rows": [],
            "p_total": p_total, "p_residential": p_residential}

    try:
        els = driver.find_elements(By.CSS_SELECTOR, "a.ctrl-link.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if txt:
                info["org"] = _normalize_org(txt)
                break
    except Exception:
        pass
    if not info["org"]:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            m = re.search(r"Наименование организации[^\n]{0,5}\n([^\n]+)", body)
            if m:
                info["org"] = _normalize_org(m.group(1).strip())
        except Exception:
            pass

    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-base__form-value.ng-binding[ng-bind='addressInfo.formattedAddress']"
        )
        if not els:
            els = driver.find_elements(
                By.CSS_SELECTOR,
                "span.form-base__form-value.ng-binding"
            )
        for el in els:
            txt = (el.text or "").strip()
            if re.search(r"\d{6},", txt):
                info["addr"] = txt
                break
    except Exception:
        pass

    info["date"] = read_contract_date(driver)
    if info["date"] == "unknown":
        info["date"] = ""

    info["pdf_icons"] = count_pdf_icons(driver)
    info["rows"] = scan_files_with_ep(driver)

    picked = pick_contract_pdf(info["rows"])
    if picked:
        info["pdf_name"] = _norm_name(picked["name"])
        info["real_pdf_name"] = _norm_name(picked.get("real_name") or "")

    return info

# ============================== СБОР ПОЛЕЙ С УФО ==============================
def collect_ep_name(driver) -> str:
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "div.text-overflow-text-plain.ng-binding"
        )
        for el in els:
            txt = (el.text or "").strip()
            low = txt.lower()
            if low.endswith(".p7s") or low.endswith(".sig"):
                return txt
    except Exception:
        pass
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        for m in re.finditer(r"[^\n]+\.(?:p7s|sig)", body, flags=re.IGNORECASE):
            line = m.group(0).strip()
            if "Электронная подпись" in line or "подпись" in line.lower():
                return line
        m = re.search(r"[^\n]+\.(?:p7s|sig)", body, flags=re.IGNORECASE)
        if m:
            return m.group(0).strip()
    except Exception:
        pass
    return ""

def collect_result_marker(driver):
    result_text = ""
    second_confirmed = False

    try:
        els = driver.find_elements(By.CSS_SELECTOR, "h2.title-h2")
        for el in els:
            txt = (el.text or "").strip()
            m = re.search(r"Подпись\s+([А-ЯЁ]+)", txt)
            if m:
                result_text = m.group(1).strip()
                break
    except Exception:
        pass
    if not result_text:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            m = re.search(r"Подпись\s+([А-ЯЁ]+)", body)
            if m:
                result_text = m.group(1).strip()
        except Exception:
            pass

    try:
        els = driver.find_elements(By.CSS_SELECTOR, "p.text-plain_bold")
        for el in els:
            txt = (el.text or "").strip().lower()
            if "электронная подпись недействительна" in txt:
                second_confirmed = True
                break
    except Exception:
        pass
    if not second_confirmed:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text.lower()
            if "электронная подпись недействительна" in body:
                second_confirmed = True
        except Exception:
            pass

    if result_text == "НЕДЕЙСТВИТЕЛЬНА":
        marker = "Ж" if second_confirmed else "Н"
    else:
        marker = None
    return result_text, marker

# ============================== DELTA ==============================
def build_delta_str(p_total: str, p_res: str) -> str:
    p_total = (p_total or "").strip()
    p_res = (p_res or "").strip()
    if not p_total or not p_res:
        return "(0)-0=0"
    try:
        v_total = float(p_total)
        v_res = float(p_res)
    except Exception:
        return "(0)-0=0"
    delta = v_total - v_res
    return f"({p_total})-{p_res}={delta:g}"

# ============================== ОСНОВНОЙ ЦИКЛ ==============================
def process_one_house(driver, index):
    wait = WebDriverWait(driver, 25)
    driver.switch_to.window(gis_handle)
    inject_timestamp_overlay(driver)
    clean_dir_files(DOWNLOADS)

    links = wait.until(EC.presence_of_all_elements_located(
        (By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]")
    ))
    if index >= len(links):
        return False

    card_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); "
            "arguments[0].click();",
            links[index]
        )
    )
    time.sleep(2)

    p_total_card, p_res_card = "", ""
    try:
        p_total_card, p_res_card = read_house_squares_card(driver)
        log(f"    [squares-card] total={p_total_card!r} res={p_res_card!r}")
    except Exception as e:
        log(f"    [squares-card] ошибка: {e}")

    # ВЕТКА 1: нет ссылки управления МКД → О
    mgmt_links = driver.find_elements(
        By.XPATH, "//a[contains(., 'Информация об управлении МКД')]"
    )
    if not mgmt_links:
        log("    [branch] нет ссылки «Информация об управлении МКД» → О")
        addr = read_page_addr_card(driver)
        org = read_page_org_card(driver)

        p_total = p_total_card or ""
        p_res = p_res_card or ""
        delta_str = build_delta_str(p_total, p_res)

        _scroll_banner_away(driver)

        scr_a = snapshot(driver, "01_mkd", "pending",
                         "МКД (нет упр.), дата неизв.")

        if scr_a:
            write_contract_log_EO(
                scr_a=scr_a,
                org=org or "?",
                addr=addr or "?",
                pdf_label="Договор не размещен",
                date_label="Дата отсутствует",
                synopsis="информация о договоре и ЭП не размещена",
                marker="О",
                delta_str=delta_str,
            )
        else:
            log("    [contract-log] нет скрина — не пишем")

        close_tabs_except(driver, [ufo_handle, gis_handle])
        driver.switch_to.window(gis_handle)
        inject_timestamp_overlay(driver)
        log_line("O_NO_LINK", "unknown", "-")
        return True

    mgmt_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script("arguments[0].click();", mgmt_links[0])
    )

    contract_date = read_contract_date(driver)
    time.sleep(2)

    sq = _read_4_squares_mgmt(driver)

    p_total = p_total_card or sq.get("p_total") or ""
    p_res = p_res_card or sq.get("p_res") or ""
    log(f"    [squares-final] total={p_total!r} res={p_res!r}")

    house_info = collect_house_info(driver, p_total=p_total,
                                    p_residential=p_res)
    log(f"    house_info: org={house_info['org']!r} "
        f"addr={house_info['addr']!r} "
        f"pdf={house_info['pdf_name']!r} "
        f"real={house_info['real_pdf_name']!r} "
        f"date={house_info['date']!r} "
        f"pdf_icons={house_info['pdf_icons']}")

    for r in house_info["rows"]:
        log(f"      row: name={r['name']!r} real={r['real_name']!r} "
            f"ep={r['ep_state']} pdf={r['is_pdf']} "
            f"contract={r['is_contract']}")

    pdf_icons = house_info["pdf_icons"]
    pdf_count = sum(1 for r in house_info["rows"] if r["is_pdf"])

    # ВЕТКА 2: pdf_icons=0 → О
    if pdf_icons == 0:
        log("    [branch] pdf_icons=0 → О")
        _scroll_banner_away(driver)
        scr_a = snapshot(driver, "01_mkd", "pending",
                         f"МКД, дата ДУ(У)={contract_date}")
        date_label = house_info["date"] or "Дата отсутствует"
        delta_str = build_delta_str(p_total, p_res)

        if scr_a and house_info["org"] and house_info["addr"]:
            write_contract_log_EO(
                scr_a=scr_a,
                org=house_info["org"],
                addr=house_info["addr"],
                pdf_label="Договор не размещен",
                date_label=date_label,
                synopsis="информация о договоре и ЭП не размещена",
                marker="О",
                delta_str=delta_str,
            )
        else:
            log("    [contract-log] условий нет — не пишем")

        close_tabs_except(driver, [ufo_handle, gis_handle])
        driver.switch_to.window(gis_handle)
        inject_timestamp_overlay(driver)
        log_line("O_NO_FILES", contract_date, "-")
        return True

    picked = pick_contract_pdf(house_info["rows"])
    if not picked:
        log("    [branch] не удалось выбрать договор → Э")
        _scroll_banner_away(driver)
        scr_a = snapshot(driver, "01_mkd", "pending",
                         f"МКД, дата ДУ(У)={contract_date}")
        date_label = house_info["date"] or "Дата отсутствует"
        pdf_label = house_info["pdf_name"] or "Договор не размещен"
        delta_str = build_delta_str(p_total, p_res)

        if scr_a and house_info["org"] and house_info["addr"]:
            write_contract_log_EO(
                scr_a=scr_a,
                org=house_info["org"],
                addr=house_info["addr"],
                pdf_label=pdf_label,
                date_label=date_label,
                synopsis="ЭП к договору отсутствует",
                marker="Э",
                delta_str=delta_str,
            )
        close_tabs_except(driver, [ufo_handle, gis_handle])
        driver.switch_to.window(gis_handle)
        inject_timestamp_overlay(driver)
        log_line("E_UNKNOWN", contract_date, "-")
        return True

    pdf_name = _norm_name(picked["name"])
    real_pdf_name = _norm_name(picked.get("real_name") or "")
    ep_state = picked["ep_state"]
    log(f"    [branch] договор={pdf_name!r} real={real_pdf_name!r} "
        f"ep={ep_state} pdf_count={pdf_count}")

    # ВЕТКА 3: серая ЭП → Э
    if ep_state != "present":
        log("    [branch] договор без цветной ЭП → Э")
        _scroll_banner_away(driver)
        scr_a = snapshot(driver, "01_mkd", "pending",
                         f"МКД, дата ДУ(У)={contract_date}")
        date_label = house_info["date"] or "Дата отсутствует"
        delta_str = build_delta_str(p_total, p_res)

        if scr_a and house_info["org"] and house_info["addr"]:
            write_contract_log_EO(
                scr_a=scr_a,
                org=house_info["org"],
                addr=house_info["addr"],
                pdf_label=pdf_name,
                date_label=date_label,
                synopsis="ЭП к договору отсутствует",
                marker="Э",
                delta_str=delta_str,
            )
        else:
            log("    [contract-log] условий нет — не пишем")

        close_tabs_except(driver, [ufo_handle, gis_handle])
        driver.switch_to.window(gis_handle)
        inject_timestamp_overlay(driver)
        log_line("E_NO_SIGN", contract_date, "-")
        return True

    # ВЕТКА 4: УФО
    log("    [branch] договор с цветной ЭП → УФО")
    _scroll_banner_away(driver)
    scr_mkd = snapshot(driver, "01_mkd", "pending",
                       f"МКД, дата ДУ(У)={contract_date}")
    time.sleep(0.5)

    download_btn = None
    try:
        download_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[class*='downloadAllFiles']")
            )
        )
    except TimeoutException:
        log("    кнопка 'Скачать все' не найдена")

    pdf = None
    p7s = None
    zip_file = None
    zip_failed_fallback = False

    use_zip = (download_btn is not None and pdf_count == 1)

    if not use_zip:
        reason = ("нет кнопки" if download_btn is None
                  else f"в архиве ожидается >1 PDF (pdf_count={pdf_count})")
        log(f"    пофайлово ({reason})")
        clean_dir_files(DOWNLOADS)
        fb_pair = download_files_individually(driver, wait, pdf_name)
        if fb_pair is None:
            log("    пофайловое скачивание не сработало — NO_DOWNLOAD_BTN")
            close_tabs_except(driver, [ufo_handle, gis_handle])
            driver.switch_to.window(gis_handle)
            inject_timestamp_overlay(driver)
            log_line("NO_DOWNLOAD_BTN", contract_date, "-")
            return True
        pdf_dl, p7s_dl = fb_pair
        try:
            shutil.move(str(pdf_dl), str(IN_DIR / pdf_dl.name))
            shutil.move(str(p7s_dl), str(IN_DIR / p7s_dl.name))
        except Exception as e:
            log(f"    перенос в IN_DIR: ошибка: {e}")
            clean_dir_files(DOWNLOADS)
            close_tabs_except(driver, [ufo_handle, gis_handle])
            driver.switch_to.window(gis_handle)
            inject_timestamp_overlay(driver)
            log_line("NO_DOWNLOAD_BTN", contract_date, "-")
            return True
        clean_dir_files(DOWNLOADS)
        pdf = IN_DIR / pdf_dl.name
        p7s = IN_DIR / p7s_dl.name
        log(f"    скачаны пофайлово: {pdf.name!r} + {p7s.name!r}")

    else:
        driver.execute_script("arguments[0].click();", download_btn)
        time.sleep(3)

        zip_file = wait_file(DOWNLOADS,
                             r"Документы из ГИС ЖКХ.*\.zip", timeout=30)

        if zip_file is None:
            log("    ZIP не скачался → fallback пофайлово")
            _return_to_mgmt_tab(driver, mgmt_handle)
            clean_dir_files(DOWNLOADS)
            fb_pair = download_files_individually(driver, wait, pdf_name)
            if fb_pair is None:
                log("    fallback тоже не сработал — NO_DOWNLOAD_DBL")
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_DOWNLOAD_DBL", contract_date, "-")
                return True
            pdf_dl, p7s_dl = fb_pair
            try:
                shutil.move(str(pdf_dl), str(IN_DIR / pdf_dl.name))
                shutil.move(str(p7s_dl), str(IN_DIR / p7s_dl.name))
            except Exception as e:
                log(f"    перенос в IN_DIR: ошибка: {e}")
                clean_dir_files(DOWNLOADS)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_DOWNLOAD_BTN", contract_date, "-")
                return True
            clean_dir_files(DOWNLOADS)
            pdf = IN_DIR / pdf_dl.name
            p7s = IN_DIR / p7s_dl.name
            zip_failed_fallback = True
            log(f"    fallback OK: {pdf.name!r} + {p7s.name!r}")

        elif not extract_zip(zip_file, IN_DIR):
            log("    ZIP не распаковался → fallback пофайлово")
            try:
                zip_file.unlink()
            except Exception:
                pass
            clean_dir_files(IN_DIR)
            clean_dir_files(DOWNLOADS)
            _return_to_mgmt_tab(driver, mgmt_handle)
            fb_pair = download_files_individually(driver, wait, pdf_name)
            if fb_pair is None:
                log("    fallback тоже не сработал — NO_DOWNLOAD_DBL")
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_DOWNLOAD_DBL", contract_date, "-")
                return True
            pdf_dl, p7s_dl = fb_pair
            try:
                shutil.move(str(pdf_dl), str(IN_DIR / pdf_dl.name))
                shutil.move(str(p7s_dl), str(IN_DIR / p7s_dl.name))
            except Exception as e:
                log(f"    перенос в IN_DIR: ошибка: {e}")
                clean_dir_files(DOWNLOADS)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_DOWNLOAD_BTN", contract_date, "-")
                return True
            clean_dir_files(DOWNLOADS)
            pdf = IN_DIR / pdf_dl.name
            p7s = IN_DIR / p7s_dl.name
            zip_failed_fallback = True
            log(f"    fallback OK: {pdf.name!r} + {p7s.name!r}")

        else:
            pairs = find_pdf_p7s_pair_single(IN_DIR)
            if pairs:
                pdf, p7s = pairs[0]
                log(f"    [zip] single: {pdf.name!r} + {p7s.name!r}")
            else:
                disk_pdf_name = real_pdf_name or pdf_name
                log(f"    [zip] ищем пару по реальному имени: {disk_pdf_name!r}")
                pairs_m, pdf_path_m = find_pdf_p7s_pair_multi(IN_DIR,
                                                              disk_pdf_name)
                if not pairs_m or pairs_m[0][1] is None:
                    log("    пара PDF+P7S не найдена в архиве "
                        "→ fallback пофайлово")
                    move_to_old([zip_file])
                    clean_dir_files(IN_DIR)
                    clean_dir_files(DOWNLOADS)
                    _return_to_mgmt_tab(driver, mgmt_handle)
                    fb_pair = download_files_individually(driver, wait, pdf_name)
                    if fb_pair is None:
                        log("    fallback тоже не сработал — NO_DOWNLOAD_DBL")
                        close_tabs_except(driver, [ufo_handle, gis_handle])
                        driver.switch_to.window(gis_handle)
                        inject_timestamp_overlay(driver)
                        log_line("NO_DOWNLOAD_DBL", contract_date, "-")
                        return True
                    pdf_dl, p7s_dl = fb_pair
                    try:
                        shutil.move(str(pdf_dl), str(IN_DIR / pdf_dl.name))
                        shutil.move(str(p7s_dl), str(IN_DIR / p7s_dl.name))
                    except Exception as e:
                        log(f"    перенос в IN_DIR: ошибка: {e}")
                        clean_dir_files(DOWNLOADS)
                        close_tabs_except(driver, [ufo_handle, gis_handle])
                        driver.switch_to.window(gis_handle)
                        inject_timestamp_overlay(driver)
                        log_line("NO_DOWNLOAD_BTN", contract_date, "-")
                        return True
                    clean_dir_files(DOWNLOADS)
                    pdf = IN_DIR / pdf_dl.name
                    p7s = IN_DIR / p7s_dl.name
                    zip_failed_fallback = True
                    log(f"    fallback OK: {pdf.name!r} + {p7s.name!r}")
                else:
                    pdf, p7s = pairs_m[0]
                    log(f"    [zip] multi: {pdf.name!r} + {p7s.name!r}")

    # ОТПРАВКА В УФО
    file_stem = pdf.stem
    file_size = 0
    try:
        file_size = pdf.stat().st_size
    except Exception:
        pass

    try:
        with open(SEQ_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"# {file_stem} | pending_01_mkd\n")
    except Exception:
        pass

    driver.switch_to.window(ufo_handle)
    inject_timestamp_overlay(driver)
    set_ufo_zoom(driver)

    pdf_input = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR,
         "lib-file-uploader[formcontrolname='dataFile'] input[type='file']")
    ))
    driver.execute_script("arguments[0].style.display='block';", pdf_input)
    pdf_input.send_keys(str(pdf))
    time.sleep(1)

    p7s_input = driver.find_element(
        By.CSS_SELECTOR,
        "lib-file-uploader[formcontrolname='signFile'] input[type='file']"
    )
    driver.execute_script("arguments[0].style.display='block';", p7s_input)
    p7s_input.send_keys(str(p7s))
    time.sleep(3)

    ep_name = collect_ep_name(driver)
    log(f"    ep_name: {ep_name!r}")

    scr_ufo_loaded = snapshot(driver, "02_ufo_loaded", file_stem,
                              f"{file_stem} | ДУ(У)={contract_date}")
    time.sleep(0.5)

    check_btn = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "button[type='submit'].white.button")
    ))
    check_btn.click()

    result_sign = "-"
    marker = None
    scr_ufo_result = None
    result_text = ""

    try:
        wait.until(EC.presence_of_element_located(
            (By.XPATH,
             "//*[contains(normalize-space(.), "
             "'Отчет о проверке квалифицированной электронной подписи')]")
        ))
        time.sleep(2)

        result_text, marker = collect_result_marker(driver)
        log(f"    result_text: {result_text!r}  marker: {marker!r}")

        scr_ufo_result = snapshot(
            driver, "03_ufo_result", file_stem,
            f"{file_stem} | ДУ(У)={contract_date} | результат={result_text}"
        )
        time.sleep(0.5)

        if result_text == "ДЕЙСТВИТЕЛЬНА":
            result_sign = "+"
        elif result_text == "НЕДЕЙСТВИТЕЛЬНА":
            result_sign = "-"
    except Exception:
        result_text = ""
        marker = None

    scr_pdf_collage = None
    try:
        if pdf is not None and pdf.exists():
            scr_pdf_collage = _do_pdf_collage_scrn(
                driver, pdf, file_stem, file_size,
                extra=f"{file_stem} | ДУ(У)={contract_date}"
            )
        else:
            log("    [collage] PDF недоступен — пропуск")
    except Exception as e:
        log(f"    [collage] ошибка: {type(e).__name__}: {e}")

    if scr_pdf_collage and _has_ufo_text(IMG_DIR / scr_pdf_collage):
        log("    [pdf-check] в чанке коллажа есть "
            "'квалифицированной электронной подписи' — повтор")
        try:
            (IMG_DIR / scr_pdf_collage).unlink()
        except Exception:
            pass
        scr_pdf_collage = None
        try:
            if pdf is not None and pdf.exists():
                scr_pdf_collage = _do_pdf_collage_scrn(
                    driver, pdf, file_stem, file_size,
                    extra=f"{file_stem} | ДУ(У)={contract_date}"
                )
        except Exception as e:
            log(f"    [collage] повтор упал: {type(e).__name__}: {e}")
        if scr_pdf_collage and _has_ufo_text(IMG_DIR / scr_pdf_collage):
            log("    [pdf-check] повтор тоже неудачен — коллаж исключён")
            try:
                (IMG_DIR / scr_pdf_collage).unlink()
            except Exception:
                pass
            scr_pdf_collage = None

    delta_str = build_delta_str(p_total, p_res)
    log(f"    [delta] {delta_str!r}")

    try:
        if (marker in ("Ж", "Н")
                and scr_mkd and scr_ufo_loaded and scr_ufo_result
                and house_info["org"]
                and house_info["addr"]
                and house_info["pdf_name"]
                and house_info["date"]
                and ep_name):
            if contract_log_written(house_info["org"],
                                    house_info["date"],
                                    house_info["pdf_name"],
                                    house_info["addr"]):
                log("    [contract-log] already written, skip")
            else:
                write_contract_log(
                    scr_mkd, scr_ufo_loaded, scr_ufo_result,
                    house_info["org"],
                    house_info["addr"],
                    house_info["pdf_name"],
                    house_info["date"],
                    synopsis=ep_name,
                    marker=marker,
                    scr_d=scr_pdf_collage or "",
                    scr_e="",
                    delta_str=delta_str,
                )
        else:
            log("    [contract-log] conditions not met, skip")
    except Exception as e:
        log(f"    [contract-log] error: {e}")

    if scr_pdf_collage:
        try:
            driver.get(UFO_URL)
            time.sleep(2)
        except Exception as e:
            log(f"    [collage] возврат на УФО упал: {e}")
    else:
        try:
            back_link = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(., 'Назад')]")
                )
            )
            driver.execute_script("arguments[0].click();", back_link)
            time.sleep(1)
        except Exception:
            driver.get(UFO_URL)
            time.sleep(1)

    set_ufo_zoom(driver)
    inject_timestamp_overlay(driver)

    try:
        driver.execute_script("""
            document.querySelectorAll("lib-file-uploader input[type='file']")
                .forEach(el => { el.value = ''; });
        """)
    except Exception:
        pass
    time.sleep(2)

    move_to_old([pdf, p7s, zip_file])
    clean_dir_files(IN_DIR)
    clean_dir_files(DOWNLOADS)

    close_tabs_except(driver, [ufo_handle, gis_handle])
    driver.switch_to.window(gis_handle)
    inject_timestamp_overlay(driver)

    if log_already_written(file_stem):
        log(f"    [log] {file_stem} already logged")
    else:
        if zip_failed_fallback:
            log_line(file_stem, contract_date,
                     f"ZIP_FAIL_FALLBACK_OK ({result_sign})")
        else:
            log_line(file_stem, contract_date, result_sign)

    return True

def next_page(driver) -> bool:
    driver.switch_to.window(gis_handle)
    try:
        link = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(@ng-click, 'nextPage')]")
        ))
        driver.execute_script("arguments[0].click();", link)
        time.sleep(random.uniform(*PAUSE_BETWEEN_PAGES))
        inject_timestamp_overlay(driver)
        return True
    except Exception:
        return False

# ============================== MAIN ==============================
def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": str(DOWNLOADS),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    return driver

def main():
    global ufo_handle, gis_handle
    ogrns = read_ogrns(OGRN_FILE)
    print(f"=== START === OGRNs in file: {len(ogrns)}")

    driver = None
    total_mkd = 0
    ok_uk = 0
    nf_uk = 0
    err_uk = 0

    try:
        driver = build_driver()
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        driver.get(UFO_URL)
        time.sleep(2)
        ufo_handle = driver.current_window_handle
        inject_timestamp_overlay(driver)
        set_ufo_zoom(driver)

        driver.execute_script("window.open(arguments[0], '_blank');", GIS_URL)
        gis_handle = driver.window_handles[-1]
        driver.switch_to.window(gis_handle)
        time.sleep(3)
        inject_timestamp_overlay(driver)
        open_uk_tab(driver, wait)

        for idx, ogrn in enumerate(ogrns, 1):
            setup_logs(ogrn)
            log(f"--- [{idx}/{len(ogrns)}] OGRN {ogrn} ---")
            try:
                driver.switch_to.window(gis_handle)

                if not select_org(driver, wait, ogrn):
                    nf_uk += 1
                    log(f"    RESULT not_found. TOTAL_MKD so far = {total_mkd}")
                    continue

                total_records = read_total_records(driver)
                log(f"    Всего записей (со страницы): {total_records}")

                if not set_page_size_100(driver, wait):
                    err_uk += 1
                    log(f"    RESULT page_size_fail. TOTAL_MKD so far = {total_mkd}")
                    continue

                index = 0
                uk_mkd = 0
                while True:
                    try:
                        ok = process_one_house(driver, index)
                    except KeyboardInterrupt:
                        log("interrupted by user")
                        return
                    except Exception as e:
                        log(f"    EXCEPTION house index={index}: "
                            f"{type(e).__name__}: {e}")
                        try:
                            close_tabs_except(driver, [ufo_handle, gis_handle])
                            driver.switch_to.window(gis_handle)
                            inject_timestamp_overlay(driver)
                        except Exception:
                            pass
                        ok = True
                    if not ok:
                        if not next_page(driver):
                            break
                        index = 0
                        continue
                    index += 1
                    uk_mkd += 1
                    total_mkd += 1

                ok_uk += 1
                log(f"    RESULT ok: mkd={uk_mkd}. TOTAL_MKD so far = {total_mkd}")

                if total_records > 0 and uk_mkd != total_records:
                    log(f"    !!! РАСХОЖДЕНИЕ: обработано {uk_mkd} "
                        f"из {total_records}")

                try:
                    cross = click_diagonal_cross(driver)
                    log(f"    cross: {cross}")
                    time.sleep(0.5)
                except Exception as e:
                    log(f"    cross diagnostics failed: {e}")

            except KeyboardInterrupt:
                log("interrupted by user (outer)")
                return
            except Exception as e:
                err_uk += 1
                log(f"    EXCEPTION: {type(e).__name__}: {e}")
                log(f"    RESULT error. TOTAL_MKD so far = {total_mkd}")

            if idx < len(ogrns):
                pause = random.uniform(*PAUSE_BETWEEN_UK)
                log(f"    pause {pause:.1f}s ...")
                try:
                    time.sleep(pause)
                except KeyboardInterrupt:
                    log("interrupted by user (during pause)")
                    return

    finally:
        if driver is not None:
            try:
                driver.quit()
                log("[dbg] browser closed")
            except Exception:
                pass

    log(f"=== END === ok_uk={ok_uk} not_found_uk={nf_uk} "
        f"errors_uk={err_uk} TOTAL_MKD={total_mkd}")

if __name__ == "__main__":
    main()