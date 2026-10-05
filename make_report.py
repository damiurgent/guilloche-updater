# -*- coding: utf-8 -*-
"""
gisjkh-ufo.py — полный прогон по списку ОГРН из ogrns.txt.

Правило: дом пишется в contracts.log только если одновременно:
    - ЭП недействительна (marker 'Н' или 'Ж'),
    - найдена валидная пара PDF+P7S одним из способов:
        * single: ровно 1 PDF + 1 P7S, имена совпадают;
        * multi:  > 1 PDF, найден договор по маркеру 'договор|ду',
                  при нескольких кандидатах отсеиваются допы
                  (по маркерам 'к ду' / 'к договор'); должен остаться ровно 1;
        * fallback: кнопки 'Скачать все' нет — качаем PDF и P7S по отдельности.

Дополнительно:
    - 4-й скриншот — первая страница PDF-договора (04_pdf_first),
      через data:text/html;base64 в текущей вкладке УФО.
    - Площади из карточки дома (totalSquare, residentialSquare)
      читаются СРАЗУ после открытия карточки дома (card_handle),
      до перехода на "Информацию об управлении МКД".
      Пишутся во вторую строку блока contracts.log:
         scrNNNN.jpg - (<Pз>)-<Pж>=<Δ> -- <ep_name>
      где Δ = Pз − Pж. В отчёт идёт только если Δ < 0.

Формат contracts.log (одна запись = 4 или 5 строк + пустая):
    scr_a - <org> -- <addr> --- <pdf_name> ---- <date>
    scr_b - (<Pз>)-<Pж>=<Δ> -- <ep_name>
    scr_c - <marker>
    [scr_pdf]
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

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
IN_DIR = BASE_DIR / "IN"
OLD_DIR = BASE_DIR / "OLD"
JPG_DIR = BASE_DIR / "JPG"
LOG_DIR = BASE_DIR / "LOG"

DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads"
WINRAR = r"C:\Program Files\WinRAR\WinRAR.exe"

for d in (BASE_DIR, IN_DIR, OLD_DIR, JPG_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ============================== НАСТРОЙКИ ==============================
OGRN_FILE = Path(__file__).resolve().parent / "ogrns.txt"

GIS_URL = "https://my.dom.gosuslugi.ru/#!/houses"
UFO_URL = "https://e-trust.gosuslugi.ru/check/sign"

LOG_FILE = LOG_DIR / "gisjkh-ufo.log"
SEQ_LOG = LOG_DIR / "sequence.txt"
CONTRACT_LOG = LOG_DIR / "contracts.log"

COPYRIGHT = "Copyright 2026 \u00a9 damiurg by \u043c\u043b\u0445/\u05de\u05dc\u05da Living Private Trust"
TZ_NAME = "Asia/Omsk"
JPEG_QUALITY = 95

WAIT_TIMEOUT = 25
PAUSE_BETWEEN_PAGES = (2, 4)
PAUSE_BETWEEN_UK = (5, 10)

EP_PREFIX_RE = re.compile(
    r"^Электронная подпись\s+оператора\s+ГИС\s+ЖКХ\s+\d{2}\.\d{2}\.\d{4}\s+",
    flags=re.IGNORECASE
)

CONTRACT_RE = re.compile(r"договор|(?<![а-яё])ду(?![а-яё])", re.IGNORECASE)
ADDENDUM_RE = re.compile(r"к\s*ду|к\s*договор", re.IGNORECASE)
RE_FACTUAL = re.compile(r"^(.*?)\s*\(\s*\d")

FB_WAIT_RENDER = 5
FB_WAIT_AFTER_CLICK = 7
FB_PDF_MARKERS = ("договор", "ду")

PDF_FIRST_W = 1920
PDF_FIRST_H = 953
PDF_FIRST_DPI = 150

# ============================== PIEXIF ==============================
try:
    import piexif
    _HAS_PIEXIF = True
except ImportError:
    _HAS_PIEXIF = False
    print("[!] piexif не установлен. EXIF писаться не будет. "
          "Установи: pip install piexif")

# ============================== PDF RENDER ==============================
try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False
    print("[!] PyMuPDF не установлен. Скриншот первой страницы PDF "
          "делаться не будет. Установи: pip install pymupdf")

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    print("[!] Pillow не установлен. Скриншот первой страницы PDF "
          "делаться не будет. Установи: pip install pillow")

# ============================== ЛОГ ==============================
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:
        print(f"[!] cannot write log: {e}", flush=True)

def log_line(filename_stem: str, date_str: str, result: str):
    log(f"    [result] {filename_stem} | {date_str} | {result}")

def log_already_written(filename_stem: str) -> bool:
    if not LOG_FILE.exists():
        return False
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                if f"[result] {filename_stem} |" in line:
                    return True
    except Exception:
        pass
    return False

# ============================== НОВЫЙ ЛОГ ==============================
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
    if not CONTRACT_LOG.exists():
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
                    r"scr\d{4}\.jpg\s*-\s*"
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
                       ep_name: str, marker: str,
                       scr_pdf: str = "",
                       delta_str: str = ""):
    line1 = f"{scr_a} - {org} -- {addr} --- {pdf_name} ---- {date_str}"
    if delta_str:
        line2 = f"{scr_b} - {delta_str} -- {ep_name}"
    else:
        line2 = f"{scr_b} - {ep_name}"
    line3 = f"{scr_c} - {marker}"
    line4 = scr_pdf if scr_pdf else ""
    try:
        CONTRACT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTRACT_LOG, "a", encoding="utf-8", newline="") as fh:
            fh.write(line1 + "\r\n")
            fh.write(line2 + "\r\n")
            fh.write(line3 + "\r\n")
            if line4:
                fh.write(line4 + "\r\n")
            fh.write("\r\n")
        log(f"    [contract-log] written: {line3}"
            + (f" / {line4}" if line4 else "")
            + (f"  delta={delta_str}" if delta_str else ""))
    except Exception as e:
        print(f"[!] cannot write contract log: {e}", flush=True)

# ============================== СЕКВЕНЦИЯ ==============================
def _next_seq_number() -> int:
    if not SEQ_LOG.exists():
        return 1
    maxn = 0
    try:
        with open(SEQ_LOG, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"scr(\d{4})\.jpg", line.strip())
                if m:
                    maxn = max(maxn, int(m.group(1)))
    except Exception:
        pass
    return maxn + 1

def _register_seq(scr_name: str, file_stem: str, tag: str):
    try:
        with open(SEQ_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{scr_name} | {file_stem} | {tag}\n")
    except Exception as e:
        print(f"[!] cannot write sequence log: {e}")

# ============================== SNAPSHOT ==============================
def snapshot(driver, tag: str, file_stem: str, extra: str = ""):
    try:
        n = _next_seq_number()
        scr_name = f"scr{n:04d}.jpg"
        out_path = JPG_DIR / scr_name

        tmp_png = JPG_DIR / f"_tmp_{n:04d}.png"
        ok = driver.save_screenshot(str(tmp_png))
        if not ok:
            print("[!] save_screenshot returned False")
            return None

        exif_bytes = None
        if _HAS_PIEXIF:
            try:
                now = _dt.datetime.now(ZoneInfo(TZ_NAME))
                dt_str = now.strftime("%Y:%m:%d %H:%M:%S")
                tz_str = now.strftime("%z")
                tz_formatted = f"{tz_str[0]}{tz_str[1:3]}:{tz_str[3:5]}"

                cur_url = driver.current_url or ""
                if cur_url.startswith("data:"):
                    cur_url = "http://localhost/view.htm"
                elif len(cur_url) > 900:
                    cur_url = cur_url[:900] + "..."

                extra_safe = (extra or "")[:900]

                exif_dict = {
                    "0th": {
                        piexif.ImageIFD.DateTime: dt_str.encode(),
                        piexif.ImageIFD.ImageDescription: cur_url.encode("utf-8", "ignore"),
                        piexif.ImageIFD.Copyright: COPYRIGHT.encode("utf-8", "ignore"),
                    },
                    "Exif": {
                        piexif.ExifIFD.DateTimeOriginal: dt_str.encode(),
                        piexif.ExifIFD.DateTimeDigitized: dt_str.encode(),
                        piexif.ExifIFD.OffsetTimeOriginal: tz_formatted.encode(),
                        piexif.ExifIFD.UserComment: extra_safe.encode("utf-8", "ignore"),
                    },
                    "GPS": {},
                    "1st": {},
                    "thumbnail": None,
                }
                exif_bytes = piexif.dump(exif_dict)
            except Exception as e:
                print(f"[!] EXIF prepare failed: {e}")

        try:
            from PIL import Image
            img = Image.open(tmp_png).convert("RGB")
            if exif_bytes:
                img.save(str(out_path), "JPEG",
                         quality=JPEG_QUALITY, exif=exif_bytes)
            else:
                img.save(str(out_path), "JPEG", quality=JPEG_QUALITY)
            img.close()
        finally:
            try:
                tmp_png.unlink()
            except Exception:
                pass

        _register_seq(scr_name, file_stem, tag)
        log(f"    [snap] {scr_name} ({file_stem}_{tag})")
        return scr_name
    except Exception as e:
        print(f"[!] snapshot failed: {e}")
        return None

# ============================== PDF FIRST PAGE ==============================
def _render_pdf_first_page(pdf_path: Path, out_png: Path) -> bool:
    if not _HAS_FITZ or not _HAS_PIL:
        log("    [pdf] PyMuPDF/Pillow недоступны — пропуск")
        return False
    raw_png = out_png.with_name(out_png.stem + "_raw.png")
    try:
        doc = fitz.open(str(pdf_path))
        if doc.page_count == 0:
            doc.close()
            log("    [pdf] PDF пуст")
            return False
        page = doc.load_page(0)
        zoom = PDF_FIRST_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(raw_png))
        doc.close()

        img = Image.open(raw_png).convert("RGB")
        w, h = img.size
        new_w = PDF_FIRST_W
        new_h = int(round(h * new_w / w))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        if new_h >= PDF_FIRST_H:
            img = img.crop((0, 0, new_w, PDF_FIRST_H))
        else:
            canvas = Image.new("RGB", (new_w, PDF_FIRST_H), (255, 255, 255))
            canvas.paste(img, (0, 0))
            img.close()
            img = canvas
        img.save(out_png, "PNG")
        img.close()
        log(f"    [pdf] рендер первой страницы: {out_png.name} "
            f"({out_png.stat().st_size} байт)")
        return True
    except Exception as e:
        log(f"    [pdf] рендер упал: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            if raw_png.exists():
                raw_png.unlink()
        except Exception:
            pass


def _build_pdf_view_data_url(png_path: Path) -> str:
    with open(png_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    html = (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>view</title><style>"
        "html,body{margin:0;padding:0;background:#fff;overflow:hidden;}"
        f"img{{width:{PDF_FIRST_W}px;height:{PDF_FIRST_H}px;display:block;}}"
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


def snapshot_pdf_first_page(driver, pdf_path: Path,
                            file_stem: str, extra: str = ""):
    if not _HAS_FITZ or not _HAS_PIL:
        log("    [pdf-snap] нет PyMuPDF/Pillow — пропуск")
        return None

    png_path = IN_DIR / f"_pdf_first_{file_stem}.png"
    try:
        if not _render_pdf_first_page(pdf_path, png_path):
            return None
        data_url = _build_pdf_view_data_url(png_path)
        log("    [pdf-snap] открываю data: URL в текущей вкладке")
        driver.get(data_url)
        time.sleep(1.5)
        try:
            driver.execute_script(_PDF_TS_JS)
        except Exception as e:
            log(f"    [pdf-snap] overlay: {e}")
        time.sleep(1.0)
        scr = snapshot(driver, "04_pdf_first", file_stem, extra)
        return scr
    except Exception as e:
        log(f"    [pdf-snap] упал: {type(e).__name__}: {e}")
        return None
    finally:
        try:
            if png_path.exists():
                png_path.unlink()
        except Exception:
            pass


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
        except (ElementNotInteractableException,
                StaleElementReferenceException) as e:
            return f"error:{sel}:{e}"
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

def wait_file(directory: Path, pattern: str, timeout: int = 60):
    end = time.time() + timeout
    last_size = -1
    while time.time() < end:
        candidates = [f for f in directory.iterdir() if re.search(pattern, f.name)]
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
def find_pdf_p7s_pair_single(directory: Path):
    pdfs = [f for f in directory.iterdir() if f.suffix.lower() == ".pdf"]
    p7s_list = [f for f in directory.iterdir()
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
        log(f"    [pair-single] strict mismatch: pdf='{pdf.stem}' "
            f"vs clean_p7s='{clean_base}'")
        return []
    else:
        if pdf.name in p7s.name:
            log(f"    [pair-single] soft match: pdf='{pdf.name}'")
            return [(pdf, p7s)]
        return []

def find_pdf_p7s_pair_multi(directory: Path, actual_pdf_name: str):
    pdf_path = None
    p7s_found = []
    try:
        for f in directory.iterdir():
            if not f.is_file():
                continue
            if f.name == actual_pdf_name:
                pdf_path = f
                continue
            lowname = f.name.lower()
            if lowname.endswith(".p7s") or lowname.endswith(".sig"):
                for ext in (".p7s", ".sig"):
                    if f.name.endswith(f"{actual_pdf_name}{ext}"):
                        p7s_found.append(f)
                        break
    except Exception as e:
        log(f"    [pair-multi] обход архива упал: {e}")
        return [], None

    if pdf_path is None:
        return [], None
    if len(p7s_found) != 1:
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
        print(f"[!] close_tabs_except: driver is not a webdriver, "
              f"got {type(driver).__name__}: {driver!r}", flush=True)
        return
    for h in list(driver.window_handles):
        if h not in keep_handles:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception as e:
                print(f"[!] close_tabs_except: cannot close {h}: {e}",
                      flush=True)

def open_new_tab_and_switch(driver, wait, click_fn):
    if not hasattr(driver, "window_handles"):
        raise RuntimeError(
            f"open_new_tab_and_switch: driver is not a webdriver, "
            f"got {type(driver).__name__}: {driver!r}"
        )
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
        if t is not None:
            return t.strip()
    except Exception:
        pass
    try:
        return (el.text or "").strip()
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

def download_files_individually(driver, wait) -> list:
    log("    [fb] === попытка пофайлового скачивания ===")
    time.sleep(FB_WAIT_RENDER)

    raw_names = driver.find_elements(
        By.CSS_SELECTOR,
        "div.file-panel__row-item.file-panel__name span.ng-binding"
    )
    log(f"    [fb] найдено span.ng-binding в file-panel__name: {len(raw_names)}")

    name_spans = []
    for el in raw_names:
        t = _fb_get_text(el)
        if not t:
            continue
        if not t.lower().endswith(".pdf"):
            continue
        if "(" in t:
            continue
        name_spans.append(el)

    log(f"    [fb] отобрано имён .pdf (без размера): {len(name_spans)}")
    for el in name_spans:
        log(f"        {_fb_get_text(el)!r}")

    relevant = []
    for el in name_spans:
        txt = _fb_get_text(el)
        low = txt.lower()
        if any(m in low for m in FB_PDF_MARKERS):
            relevant.append((txt, el))

    log(f"    [fb] PDF по маркерам {FB_PDF_MARKERS}: {len(relevant)}")
    for name, _ in relevant:
        log(f"        {name!r}")

    if len(relevant) != 1:
        log(f"    [fb] ожидалось 1 совпадение, получили {len(relevant)} — стоп")
        return None

    target_name, target_name_el = relevant[0]
    log(f"    [fb] ЦЕЛЬ: {target_name!r}")

    pdf_icons = driver.find_elements(By.CSS_SELECTOR, "span.icon-file_pdf")
    log(f"    [fb] найдено PDF-иконок (span.icon-file_pdf): {len(pdf_icons)}")
    if len(pdf_icons) != 1:
        log(f"    [fb] PDF-иконок={len(pdf_icons)} — не могу однозначно сопоставить")
        return None
    pdf_icon = pdf_icons[0]

    try:
        sig_icon = pdf_icon.find_element(
            By.XPATH,
            "preceding::span[contains(@class, 'gis-icon-signature')][1]"
        )
        log("    [fb] ЭП-иконка найдена через preceding:: от PDF-иконки")
    except Exception as e:
        log(f"    [fb] не удалось найти ЭП-иконку перед PDF-иконкой: {e}")
        return None

    log("    [fb] --- клик по ЭП-иконке ---")
    if not _fb_click_hoverable(driver, sig_icon, "sig"):
        log("    [fb] не удалось кликнуть по ЭП — стоп")
        return None
    log(f"    [fb] ждём {FB_WAIT_AFTER_CLICK} сек ...")
    time.sleep(FB_WAIT_AFTER_CLICK)

    log("    [fb] --- клик по ИМЕНИ PDF-файла ---")
    try:
        pdf_clickable = target_name_el.find_element(
            By.XPATH,
            "./ancestor::span[contains(@class, 'soh__trigger')][1]"
        )
        html_snippet = (pdf_clickable.get_attribute("outerHTML") or "")[:120]
        log(f"    [fb] clickable PDF: {html_snippet!r}")
    except Exception as e:
        log(f"    [fb] не удалось найти кликабельный родитель PDF: {e}")
        return None

    if not _fb_click_hoverable(driver, pdf_clickable, "pdf_name"):
        log("    [fb] не удалось кликнуть по имени PDF — стоп")
        return None
    log(f"    [fb] ждём {FB_WAIT_AFTER_CLICK} сек ...")
    time.sleep(FB_WAIT_AFTER_CLICK)

    pdf_files = [f for f in DOWNLOADS.iterdir()
                 if f.is_file() and f.suffix.lower() == ".pdf"]
    p7s_files = [f for f in DOWNLOADS.iterdir()
                 if f.is_file() and f.suffix.lower() in (".p7s", ".sig")]

    log(f"    [fb] в Downloads: pdf={len(pdf_files)} p7s={len(p7s_files)}")
    for f in pdf_files + p7s_files:
        try:
            log(f"        {f.name!r}  size={f.stat().st_size}")
        except Exception:
            pass

    if len(pdf_files) != 1 or len(p7s_files) != 1:
        log("    [fb] ожидалась ровно 1 пара PDF+P7S — не сложилось")
        return None

    return [pdf_files[0], p7s_files[0]]

# ============================== ГИС ЖКХ ==============================
def open_uk_tab(driver, wait: WebDriverWait):
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
    except (ElementNotInteractableException,
            ElementClickInterceptedException):
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

    log(f"    typed: {read_input_value(driver)!r}")

    try:
        result_link = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR,
             "ul.select2-results li.select2-result-selectable a, "
             "ul.select2-results li.select2-result-selectable")
        ))
    except TimeoutException:
        log(f"    OGRN {ogrn} not found in select2 dropdown")
        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass
        return False

    result_link.click()
    time.sleep(2)
    log(f"    chosen: {read_selected_text(driver)!r}")

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
        log(f"    no house cards appeared for {ogrn}")
        return False

def set_page_size_100(driver, wait: WebDriverWait) -> bool:
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "select#count")
    except Exception:
        candidates = []

    if not candidates:
        log("    select#count отсутствует — домов мало, пагинация не нужна")
        return True

    select_el = candidates[0]

    try:
        if not select_el.is_displayed() or not select_el.is_enabled():
            log("    select#count не виден/неактивен — пропускаем")
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

    log("    set_page_size_100: не удалось, продолжаю как есть")
    return True

def read_contract_date(driver):
    label_text = "Дата заключения договора управления"
    try:
        WebDriverWait(driver, 8).until(
            lambda d: label_text in d.find_element(By.TAG_NAME, "body").text
        )
    except Exception:
        pass
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

# ============================== ПЛОЩАДИ ИЗ КАРТОЧКИ ==============================
def _read_square(driver, ng_bind_fragment: str) -> str:
    """
    Достаёт значение из <td> по фрагменту ng-bind-html.
    Селектор без класса — на карточке дома это работает.
    Возвращает строку '714.9' или '' если не удалось.
    """
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


def read_house_squares(driver) -> tuple:
    """
    Возвращает (p_total, p_residential) — строки или ''.
    ВНИМАНИЕ: читать надо на СТРАНИЦЕ КАРТОЧКИ ДОМА
    (/house-view?...), а не на "Информации об управлении МКД".
    """
    p_total = _read_square(driver, "totalSquare")
    p_residential = _read_square(driver, "residentialSquare")
    return p_total, p_residential

# ============================== СБОР ПОЛЕЙ С ГИС ЖКХ ==============================
def count_pdf_icons(driver) -> int:
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-upload__icon.icon-file.icon-file_pdf"
        )
        return len(els)
    except Exception:
        return -1

def collect_pdf_pairs(driver) -> list:
    pairs = []
    try:
        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "div.file-panel__row.file-panel__justify-start"
        )
    except Exception:
        return pairs

    for row in rows:
        try:
            if not row.find_elements(By.CSS_SELECTOR, "span.icon-file_pdf"):
                continue
        except Exception:
            continue

        declared = ""
        try:
            candidates = row.find_elements(
                By.CSS_SELECTOR,
                ".file-panel__row-item.file-panel__name span.ng-binding"
            )
            for el in candidates:
                try:
                    style = (el.get_attribute("style") or "").lower()
                    text = (el.text or "").strip()
                except Exception:
                    continue
                if "0075c0" in style and text:
                    declared = text
                    break
            if not declared:
                for el in candidates:
                    try:
                        text = (el.text or "").strip()
                    except Exception:
                        text = ""
                    if text and text != "Документ PDF":
                        declared = text
                        break
        except Exception:
            pass

        actual = ""
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
                if not txt:
                    continue
                m = RE_FACTUAL.match(txt)
                if m:
                    actual = m.group(1).strip()
                    break
        except Exception:
            pass

        pairs.append((declared, actual))
    return pairs

def find_contract_actual_name(pairs: list):
    hits = [(d, a) for (d, a) in pairs if d and CONTRACT_RE.search(d)]
    if not hits:
        log(f"    [multi] маркер 'договор|ду' не найден среди {len(pairs)} пар")
        return ""

    if len(hits) == 1:
        d, a = hits[0]
        if not a:
            log(f"    [multi] договор найден ({d!r}), но фактическое имя пусто")
            return ""
        log(f"    [multi] договор: {d!r} -> actual={a!r}")
        return a

    log(f"    [multi] маркер 'договор|ду' найден в {len(hits)} парах:")
    for d, a in hits:
        is_add = bool(ADDENDUM_RE.search(d or ""))
        log(f"      - {d!r} -> {a!r}{'  [ДОП]' if is_add else ''}")

    main_hits = [(d, a) for (d, a) in hits
                 if not ADDENDUM_RE.search(d or "")]
    log(f"    [multi] после отсева допов осталось {len(main_hits)}")

    if len(main_hits) != 1:
        log(f"    [multi] не удалось однозначно выбрать договор — пропуск")
        return ""

    d, a = main_hits[0]
    if not a:
        log(f"    [multi] договор найден ({d!r}), но фактическое имя пусто")
        return ""
    log(f"    [multi] договор (после отсева): {d!r} -> actual={a!r}")
    return a

def collect_house_info(driver, p_total: str = "", p_residential: str = ""):
    """
    Собирает поля с "Информации об управлении МКД" (org, addr, pdf_name, date,
    pdf_icons, pairs).
    Площади приходят параметрами (их читают с карточки дома).
    """
    info = {"org": "", "addr": "", "pdf_name": "", "date": "",
            "pdf_icons": -1, "pairs": [],
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
    if not info["addr"]:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            m = re.search(r"Адрес дома[^\n]{0,5}\n([^\n]+)", body)
            if m:
                info["addr"] = m.group(1).strip()
        except Exception:
            pass

    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            ".file-panel__row-item.file-panel__name .ng-binding"
        )
        for el in els:
            title = el.get_attribute("title") or ""
            txt = (el.text or "").strip()
            candidate = title or txt
            if candidate.lower().endswith(".pdf"):
                info["pdf_name"] = candidate
                break
    except Exception:
        pass

    info["date"] = read_contract_date(driver)
    if info["date"] == "unknown":
        info["date"] = ""

    info["pdf_icons"] = count_pdf_icons(driver)
    if info["pdf_icons"] > 1:
        info["pairs"] = collect_pdf_pairs(driver)

    if info["pdf_icons"] > 1 and info["pairs"]:
        hits = [(d, a) for (d, a) in info["pairs"]
                if d and CONTRACT_RE.search(d)]
        if len(hits) == 1:
            info["pdf_name"] = hits[0][0]
            log(f"    [house] pdf_name из пары: {info['pdf_name']!r}")
        elif len(hits) > 1:
            log(f"    [house] маркер 'договор|ду' найден в {len(hits)} парах:")
            for d, a in hits:
                is_add = bool(ADDENDUM_RE.search(d or ""))
                log(f"      - {d!r} -> {a!r}{'  [ДОП]' if is_add else ''}")
            main_hits = [(d, a) for (d, a) in hits
                         if not ADDENDUM_RE.search(d or "")]
            log(f"    [house] после отсева допов осталось {len(main_hits)}")
            if len(main_hits) == 1:
                info["pdf_name"] = main_hits[0][0]
                log(f"    [house] pdf_name (после отсева допов): "
                    f"{info['pdf_name']!r}")

    if not info["pdf_name"]:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            m = re.search(r"([^\n\r\\/]{2,200}?\.pdf)", body)
            if m:
                info["pdf_name"] = m.group(1).strip()
        except Exception:
            pass

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
def build_delta_str(house_info: dict) -> str:
    p_total = (house_info.get("p_total") or "").strip()
    p_res = (house_info.get("p_residential") or "").strip()
    if not p_total or not p_res:
        return ""
    try:
        v_total = float(p_total)
        v_res = float(p_res)
    except Exception:
        return ""
    delta = v_total - v_res
    return f"({p_total})-{p_res}={delta:g}"

# ============================== ОСНОВНОЙ ЦИКЛ ==============================
def process_one_house(driver, ufo_handle, gis_handle, index):
    print(f"[dbg] process_one_house: driver={type(driver).__name__} "
          f"id={id(driver)} index={index}", flush=True)
    if not hasattr(driver, "window_handles"):
        raise RuntimeError(
            f"process_one_house: driver is not a webdriver, "
            f"got {type(driver).__name__}: {driver!r}"
        )

    wait = WebDriverWait(driver, 25)
    driver.switch_to.window(gis_handle)
    inject_timestamp_overlay(driver)
    clean_dir_files(DOWNLOADS)

    links = wait.until(EC.presence_of_all_elements_located(
        (By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]")
    ))
    if index >= len(links):
        return False

    # --- открываем карточку дома (card_handle) ---
    card_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); "
            "arguments[0].click();",
            links[index]
        )
    )
    time.sleep(2)

    # --- читаем площади СРАЗУ, пока мы на карточке дома ---
    p_total, p_res = "", ""
    try:
        p_total, p_res = read_house_squares(driver)
        log(f"    [squares] total={p_total!r} residential={p_res!r}")
    except Exception as e:
        log(f"    [squares] ошибка чтения: {e}")

    # --- переходим на "Информацию об управлении МКД" (mgmt_handle) ---
    mgmt_link = wait.until(EC.presence_of_element_located(
        (By.XPATH, "//a[contains(., 'Информация об управлении МКД')]")
    ))
    time.sleep(1)
    mgmt_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script("arguments[0].click();", mgmt_link)
    )

    contract_date = read_contract_date(driver)
    time.sleep(2)

    download_btn = None
    try:
        download_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[class*='downloadAllFiles']")
            )
        )
    except TimeoutException:
        log("    кнопка скачивания не появилась — документов нет")

    house_info = collect_house_info(driver, p_total=p_total, p_residential=p_res)
    log(f"    house_info: org={house_info['org']!r} "
        f"addr={house_info['addr']!r} "
        f"pdf={house_info['pdf_name']!r} "
        f"date={house_info['date']!r} "
        f"pdf_icons={house_info['pdf_icons']} "
        f"p_total={house_info['p_total']!r} "
        f"p_res={house_info['p_residential']!r}")

    scr_mkd = snapshot(driver, "01_mkd", "pending",
                       f"МКД, дата ДУ(У)={contract_date}")
    time.sleep(0.5)

    if house_info["pdf_icons"] == 0:
        log("    pdf_icons=0 — документов нет, дом пропускаем")
        close_tabs_except(driver, [ufo_handle, gis_handle])
        driver.switch_to.window(gis_handle)
        inject_timestamp_overlay(driver)
        log_line("NO_DOCUMENTS", contract_date, "-")
        return True

    pdf = None
    p7s = None
    zip_file = None

    if download_btn is None:
        log("    кнопки 'Скачать все' нет — пробуем пофайловое скачивание")
        clean_dir_files(DOWNLOADS)
        fb_pair = download_files_individually(driver, wait)
        if fb_pair is None:
            log("    fallback не сработал — NO_DOWNLOAD_BTN")
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
            log(f"    fallback: не удалось перенести файлы в IN_DIR: {e}")
            clean_dir_files(DOWNLOADS)
            close_tabs_except(driver, [ufo_handle, gis_handle])
            driver.switch_to.window(gis_handle)
            inject_timestamp_overlay(driver)
            log_line("NO_DOWNLOAD_BTN", contract_date, "-")
            return True
        clean_dir_files(DOWNLOADS)
        pdf = IN_DIR / pdf_dl.name
        p7s = IN_DIR / p7s_dl.name
        log(f"    fallback: пара {pdf.name} + {p7s.name} перенесена в IN_DIR")
    else:
        driver.execute_script("arguments[0].click();", download_btn)
        time.sleep(3)

        zip_file = wait_file(DOWNLOADS, r"Документы из ГИС ЖКХ.*\.zip", timeout=30)
        if zip_file is None:
            log("    ZIP не скачался")
            close_tabs_except(driver, [ufo_handle, gis_handle])
            driver.switch_to.window(gis_handle)
            inject_timestamp_overlay(driver)
            log_line("ZIP_FAIL", contract_date, "-")
            return True

        if not extract_zip(zip_file, IN_DIR):
            log("    ZIP не распаковался")
            try:
                zip_file.unlink()
            except Exception:
                pass
            close_tabs_except(driver, [ufo_handle, gis_handle])
            driver.switch_to.window(gis_handle)
            inject_timestamp_overlay(driver)
            log_line("ZIP_FAIL", contract_date, "-")
            return True

        if house_info["pdf_icons"] > 1:
            actual_pdf_name = find_contract_actual_name(house_info["pairs"])
            if not actual_pdf_name:
                log("    pdf_icons>1, но договор не определён однозначно — пропуск")
                move_to_old([zip_file])
                clean_dir_files(IN_DIR)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("MULTI_CONTRACT", contract_date, "-")
                return True
            log(f"    multi: ожидаем PDF '{actual_pdf_name}'")
            pairs, pdf_path = find_pdf_p7s_pair_multi(IN_DIR, actual_pdf_name)
            if pdf_path is None:
                log("    [multi] PDF с фактическим именем не найден — пропуск")
                move_to_old([zip_file])
                clean_dir_files(IN_DIR)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_PDF_IN_ARCHIVE", contract_date, "-")
                return True
            if not pairs:
                log("    [multi] P7S не найден или их больше одного — пропуск")
                move_to_old([zip_file])
                clean_dir_files(IN_DIR)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NO_OR_MULTI_P7S", contract_date, "-")
                return True
            pdf, p7s = pairs[0]
            log(f"    [multi] пара: {pdf.name} + {p7s.name}")
        else:
            pairs = find_pdf_p7s_pair_single(IN_DIR)
            if not pairs:
                log("    [single] NOT_A_CONTRACT: не 1 PDF + 1 P7S или имена не совпали")
                move_to_old([zip_file])
                clean_dir_files(IN_DIR)
                close_tabs_except(driver, [ufo_handle, gis_handle])
                driver.switch_to.window(gis_handle)
                inject_timestamp_overlay(driver)
                log_line("NOT_A_CONTRACT", contract_date, "-")
                return True
            pdf, p7s = pairs[0]

    file_stem = pdf.stem

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

    # --- 4-й скриншот: первая страница PDF-договора ---
    scr_pdf_first = None
    try:
        if pdf is not None and pdf.exists():
            scr_pdf_first = snapshot_pdf_first_page(
                driver, pdf, file_stem,
                f"{file_stem} | ДУ(У)={contract_date} | первая страница PDF"
            )
        else:
            log("    [pdf-snap] PDF недоступен — пропуск")
    except Exception as e:
        log(f"    [pdf-snap] ошибка: {type(e).__name__}: {e}")

    delta_str = build_delta_str(house_info)
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
                    ep_name,
                    marker,
                    scr_pdf=scr_pdf_first or "",
                    delta_str=delta_str,
                )
        else:
            log("    [contract-log] conditions not met, skip")
    except Exception as e:
        log(f"    [contract-log] error: {e}")

    # --- Возврат на УФО ---
    if scr_pdf_first:
        try:
            driver.get(UFO_URL)
            time.sleep(2)
        except Exception as e:
            log(f"    [pdf-snap] возврат на УФО упал: {e}")
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
        log_line(file_stem, contract_date, result_sign)

    return True

def next_page(driver, gis_handle) -> bool:
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
    ogrns = read_ogrns(OGRN_FILE)
    log(f"=== START === OGRNs in file: {len(ogrns)}")

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
            log(f"--- [{idx}/{len(ogrns)}] OGRN {ogrn} ---")
            try:
                driver.switch_to.window(gis_handle)

                if not select_org(driver, wait, ogrn):
                    nf_uk += 1
                    log(f"    RESULT not_found. TOTAL_MKD so far = {total_mkd}")
                    continue

                if not set_page_size_100(driver, wait):
                    err_uk += 1
                    log(f"    RESULT page_size_fail. TOTAL_MKD so far = {total_mkd}")
                    continue

                index = 0
                uk_mkd = 0
                while True:
                    try:
                        ok = process_one_house(driver, ufo_handle, gis_handle, index)
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
                        if not next_page(driver, gis_handle):
                            break
                        index = 0
                        continue
                    index += 1
                    uk_mkd += 1
                    total_mkd += 1

                ok_uk += 1
                log(f"    RESULT ok: mkd={uk_mkd}. TOTAL_MKD so far = {total_mkd}")

                try:
                    cross = click_diagonal_cross(driver)
                    log(f"    cross: {cross}")
                    time.sleep(0.5)
                    log(f"    after cross: "
                        f"chosen={read_selected_text(driver)!r} "
                        f"input={read_input_value(driver)!r}")
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
