# -*- coding: utf-8 -*-
"""
make_report2.py — генератор HTML-отчёта по проверке ЭП договоров УК.

Читает:
  %TEMP%\IN\LOG\contracts*.log       — блоки домов (УК, адрес, ЭП, маркер, дата)
  %TEMP%\IN\LOG\gisjkh-ufo*.log      — статусы ГИС ЖКХ (Д / Н / Ж)
  %TEMP%\IN\IMG\scrXXXXXX.png        — скриншоты (Description, Source в чанках)
  SCRIPT_DIR\avar.txt                — аварийные адреса (исключаются)
  SCRIPT_DIR\ogrns_lost.txt          — <ОГРН/ИНН> -- <scr.png>
  SCRIPT_DIR\fio_lost.txt            — <ФИО> -- <scr.png>
  SCRIPT_DIR\graphs.json             — графы аффилированности (build_graphs.py)
  D:\DB\XML\egrul_omsk_deep_final.xml           (полный; director/name в смешанном регистре)
  D:\DB\XML\egrul_omsk_deep_final_short.xml     (короткий; часто director пустой)
  D:\DB\XML\egrul_parse_deep_pdf_lost_short.xml (короткий; потерянные ЮЛ)

Ключевые правила:
  • ФИО в fio_lost.txt уже нормализованы find_4scr_ogrn_inn.py (КАПС, им. падеж).
    Повторно norm_fio к ним НЕ применяется.
  • director из XML нормализуется norm_fio (КАПС, им. падеж) — в XML он
    в смешанном регистре и/или родительном падеже.
  • Если в коротком XML director пустой — берём из полного XML (fallback).
  • Приоритет 1 в последнем столбце — чужой ОГРН/ИНН (ogrns_lost.txt).
  • Приоритет 2 — чужое ФИО (fio_lost.txt), найденное в ЕГРЮЛ у другого ЮЛ.
    ОГРН в этом случае НЕ выводится (ФИО может быть засвечено в нескольких ОГРН).
  • Тултип графа ищется по data-inn, а если нет — по data-fio.
"""

import re
import json
import shutil
import os
import sys
import time
import subprocess
import xml.etree.ElementTree as ET
import zlib
import struct
from datetime import date as _date
from pathlib import Path
from tempfile import gettempdir
from collections import defaultdict

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import holidays as _holidays
    RU_HOLIDAYS = _holidays.RU(years=range(2000, 2032))
    _HAS_HOLIDAYS = True
except Exception as _e:
    RU_HOLIDAYS = None
    _HAS_HOLIDAYS = False

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
LOG_DIR = BASE_DIR / "LOG"
IMG_DIR = BASE_DIR / "IMG"
OUT_HTML = IMG_DIR / "report.html"

SCRIPT_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) \
    / "Desktop" / "Selenium" / "make_report2"

DOWNLOAD_ERRORS_SRC = SCRIPT_DIR / "download_errors.html"
DOWNLOAD_ERRORS_DST = IMG_DIR / "download_errors.html"
AVAR_TXT = SCRIPT_DIR / "avar.txt"
OGRNS_LOST_TXT = SCRIPT_DIR / "ogrns_lost.txt"
FIO_LOST_TXT = SCRIPT_DIR / "fio_lost.txt"
GRAPHS_JSON = SCRIPT_DIR / "graphs.json"

EGRUL_XML = Path(r"D:\DB\XML\egrul_omsk_deep_final.xml")
EGRUL_SHORT_XML = Path(r"D:\DB\XML\egrul_omsk_deep_final_short.xml")
EGRUL_LOST_SHORT_XML = Path(r"D:\DB\XML\egrul_parse_deep_pdf_lost_short.xml")
TRASH_DIR = Path(r"D:\IN\OLD\IMG")

BLACKLIST_OGRN = {
    "1165543087999", "1085543030787", "1075590844304", "1175543038418",
    "1175543039448", "1175543939918", "1135543629720", "1435543028720",
    "1165583077480", "1195542005087"
}

# ============================== norm_fio ==============================
# Нормализует director из XML (смешанный регистр и/или родительный падеж)
# к виду, в котором хранится fio_lost.txt (КАПС, им. падеж).
try:
    sys.path.insert(0, str(SCRIPT_DIR))
    from find_4scr_ogrn_inn import norm_fio  # type: ignore
except Exception as _e:
    print(f"[!] norm_fio не импортирован ({_e}) — ФИО из XML сравниваются как есть")
    def norm_fio(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").upper().replace("Ё", "Е")).strip()

# ============================== НАСТРОЙКИ ==============================
MARKER_LONG = {
    "Н": "Подпись верна, но НЕДЕЙСТВИТЕЛЬНА",
    "Ж": "Подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА",
    "Э": "Информация ПРОТИВОРЕЧИВА или нет ЭП",
    "О": "Информация о договоре НЕ РАЗМЕЩЕНА",
}
MASS_DATE_MIN = 2
MASS_DATE_HINT = (
    "Рекомендуется проверить кворумы каждого МКД по бюллетеням в списке "
    "домов с одинаковой датой. Если бюллетеней для кворума всех домов "
    "недостаточно, отягчающими обстоятельствами могут служить: "
    "проведение голосований по протоколам собраний в офисе УК; "
    "блокирование входов в подвальные помещения в большинстве подъездов "
    "(что ограничивает право собственников на проведение собраний и "
    "на использование помещений гражданской обороны и защиты от ЧС). "
    "В таком случае пролонгация договоров может быть признана незаконной. "
    "Кроме того, завышение площади жилых помещений может быть связано "
    "с соисканием субсидий из бюджетов, а завышение ОДИ в платёжных "
    "документах ведёт к необоснованному увеличению платежей собственников."
)

# ============================== ОПРОС ==============================
def ask_yes_no(prompt, default="n", timeout=10):
    default = default.lower()
    print(f"{prompt} (y/n) [{default}] — таймаут {timeout}с: ", end="", flush=True)
    if msvcrt is None:
        try: ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt): ans = default
        return ans in ("y", "yes", "д", "да")

    start = time.time()
    answer = ""
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                break
            if ch == "\x1b":
                print()
                answer = default
                break
            if ch in ("\b", "\x7f"):
                if answer:
                    answer = answer[:-1]
                    print("\b \b", end="", flush=True)
                continue
            if ch.isprintable():
                answer += ch
                print(ch, end="", flush=True)
        if time.time() - start > timeout:
            print()
            answer = default
            break
        time.sleep(0.05)

    answer = (answer or default).strip().lower()
    return answer in ("y", "yes", "д", "да")

def run_script(script_name):
    script_dir = Path(__file__).parent
    script_path = script_dir / script_name
    if not script_path.exists():
        print(f"[!] Не найден скрипт: {script_path}")
        return False
    print(f"\n=== Запуск {script_name} ===")
    try:
        result = subprocess.run([sys.executable, str(script_path)], cwd=str(script_dir),
                                check=False)
        if result.returncode != 0:
            print(f"[!] {script_name} завершился с кодом {result.returncode}")
            return False
        return True
    except Exception as e:
        print(f"[!] Ошибка запуска {script_name}: {e}")
        return False

# ============================== PNG ==============================
def read_png_chunks(img_path: Path) -> dict:
    if not img_path.exists(): return {}
    texts = {}
    try:
        with open(img_path, "rb") as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n': return {}
            while True:
                length_bytes = f.read(4)
                if not length_bytes or len(length_bytes) < 4: break
                length = struct.unpack(">I", length_bytes)[0]
                chunk_type = f.read(4)
                if not chunk_type or len(chunk_type) < 4: break
                data = f.read(length); f.read(4)
                if chunk_type in (b'tEXt', b'iTXt', b'zTXt'):
                    if chunk_type == b'zTXt':
                        parts = data.split(b'\x00', 1)
                        if len(parts) == 2:
                            kw = parts[0].decode('latin-1', errors='replace')
                            texts[kw] = zlib.decompress(parts[1][1:]).decode('utf-8', errors='replace')
                    elif chunk_type == b'tEXt':
                        parts = data.split(b'\x00', 1)
                        if len(parts) == 2:
                            kw = parts[0].decode('latin-1', errors='replace')
                            texts[kw] = parts[1].decode('latin-1', errors='replace')
                    elif chunk_type == b'iTXt':
                        parts = data.split(b'\x00')
                        if len(parts) >= 2:
                            kw = parts[0].decode('latin-1', errors='replace')
                            texts[kw] = parts[-1].decode('utf-8', errors='replace')
    except Exception:
        pass
    return texts

def parse_area_val(text: str) -> float:
    if not text: return 0.0
    text = text.strip().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        try: return float(m.group(1))
        except ValueError: pass
    return 0.0

def extract_areas_from_desc(desc: str):
    if not desc: return 0.0, 0.0, 0.0
    def get_val(pattern):
        m = re.search(pattern, desc, re.IGNORECASE | re.DOTALL)
        return parse_area_val(m.group(1)) if m else 0.0

    total = get_val(r"Общая площадь дома,?\s*кв\.?м:?\s*([\d\.\,]+)")
    living = get_val(r"Общая площадь жилых помещений,?\s*кв\.?м:?\s*([\d\.\,]+)")
    non_living = get_val(r"Общая площадь нежилых помещений,?\s*кв\.м:?\s*([\d\.\,]+)")
    return total, living, non_living

# ============================== OGRNS_LOST / FIO_LOST ==============================
def _load_two_col_map(path: Path) -> dict:
    result = {}
    if not path.exists(): return result
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or " -- " not in line: continue
                val, scr = line.split(" -- ", 1)
                val, scr = val.strip(), scr.strip()
                if val and scr:
                    result[scr] = val
    except Exception:
        pass
    return result

def load_ogrns_lost(path: Path) -> dict:
    return _load_two_col_map(path)

def load_fio_lost(path: Path) -> dict:
    return _load_two_col_map(path)

# ============================== ЕГРЮЛ ==============================
def load_egrul_directors(xml_path: Path) -> dict:
    result = {}
    if not xml_path.exists(): return result
    try: tree = ET.parse(str(xml_path))
    except Exception: return result
    for ent in tree.getroot().findall("entity"):
        ogrn_el = ent.find("ogrn")
        dir_el = ent.find("director")
        if ogrn_el is None or not (ogrn_el.text or "").strip(): continue
        ogrn = ogrn_el.text.strip()
        director = (dir_el.text or "").strip() if dir_el is not None else ""
        result[ogrn] = director
    return result

def load_egrul_short_data(xml_path: Path) -> dict:
    result = {}
    if not xml_path.exists(): return result
    try: tree = ET.parse(str(xml_path))
    except Exception: return result
    for ent in tree.getroot().findall("entity"):
        ogrn_el = ent.find("ogrn")
        if ogrn_el is None or not (ogrn_el.text or "").strip(): continue
        ogrn = ogrn_el.text.strip()
        name = (ent.findtext("name") or "").strip()
        director = (ent.findtext("director") or "").strip()
        dir_inn = (ent.findtext("director_inn") or "").strip()
        address = (ent.findtext("address") or "").strip()
        term_date = (ent.findtext("termination_date") or "").strip()
        ul_inn = (ent.findtext("ul_inn") or "").strip()
        capital = (ent.findtext("capital") or "").strip()
        result[ogrn] = {
            "ogrn": ogrn, "name": name, "director": director,
            "director_inn": dir_inn, "address": address,
            "termination_date": term_date, "ul_inn": ul_inn, "capital": capital
        }
    return result

# ============================== AVAR ==============================
def load_avar(path: Path) -> set:
    result = set()
    if not path.exists(): return result
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                addr = line.strip()
                if addr: result.add(addr)
    except Exception:
        pass
    return result

# ============================== КОНТРАКТЫ ==============================
RE_SCR = r"scr(?:\d{6}|XXXX)\.png"
RE_LINE1 = re.compile(r"^(" + RE_SCR + r")\s*-\s*(.+?)\s*--\s*(.+?)\s*---\s*(.+?)\s*----\s*(.+)$")
RE_LINE2 = re.compile(r"^(" + RE_SCR + r")\s*-\s*(?:(.+?)\s*--\s*)?(.+)$")
RE_LINE3 = re.compile(r"^(" + RE_SCR + r")\s*-\s*(.+)$")
RE_LINE4_SCR = re.compile(r"^(" + RE_SCR + r")\s*$")
RE_DATE_DDMMYYYY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
RE_FILENAME_OGRN = re.compile(r"^contracts(\d{10,})\.log$")
RE_SCR_IN_ERRLOG = re.compile(r"(scr\d{6}\.png)")

def _iter_contract_files():
    return sorted(LOG_DIR.glob("contracts*.log"), key=lambda p: p.name)

def _extract_ogrn(filename: str) -> str:
    m = RE_FILENAME_OGRN.match(filename)
    return m.group(1) if m else ""

def _is_holiday(date_str: str) -> bool:
    if not date_str: return False
    m = RE_DATE_DDMMYYYY.match(date_str.strip())
    if not m: return False
    try: d = _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except Exception: return False
    if _HAS_HOLIDAYS and RU_HOLIDAYS is not None:
        try:
            if d in RU_HOLIDAYS: return True
        except Exception:
            pass
    if d.weekday() >= 5: return True
    return False

def _is_real_scr(name: str) -> bool:
    return bool(re.match(r"^scr\d{6}\.png$", name)) if name else False

def parse_contracts_logs():
    records = []
    for path in _iter_contract_files():
        ogrn = _extract_ogrn(path.name)
        if ogrn in BLACKLIST_OGRN: continue
        try: file_mtime = path.stat().st_mtime
        except Exception: file_mtime = 0.0
        text = path.read_text(encoding="utf-8")
        blocks = re.split(r"\r?\n\r?\n", text.strip())
        for block in blocks:
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
            if len(lines) < 3: continue
            m1 = RE_LINE1.match(lines[0].strip())
            if not m1: continue
            scr_a, org, addr, pdf_name, date_str = m1.groups()
            org_clean = (org or "").strip()
            if (not org_clean) or (org_clean == "Адрес дома:") or (org_clean == "?"): continue
            if "Тюмен" in addr.upper() or addr.strip().startswith("626"): continue
            org = org_clean.upper()
            m2 = RE_LINE2.match(lines[1].strip())
            if not m2: continue
            scr_b, ep_name = m2.group(1), m2.group(3)
            m3 = RE_LINE3.match(lines[2].strip())
            if not m3: continue
            scr_c, marker = m3.groups()
            screens = [scr_a, scr_b, scr_c]
            for ln in lines[3:]:
                m = RE_LINE4_SCR.match(ln.strip())
                if m: screens.append(m.group(1))

            has_real_screens = any(_is_real_scr(s) for s in screens)
            hol = _is_holiday(date_str)

            records.append({
                "src": path.name, "ogrn": ogrn, "scr_a": scr_a, "org": org,
                "addr": addr, "pdf_name": pdf_name, "date": date_str,
                "scr_b": scr_b, "ep_name": ep_name, "scr_c": scr_c, "marker": marker,
                "marker_long": MARKER_LONG.get(marker, marker), "screens": screens,
                "has_real_screens": has_real_screens,
                "scr_pdf": screens[3] if len(screens) > 3 else "",
                "is_holiday": hol, "_mtime": file_mtime,
                "total_area": 0.0, "living_area": 0.0, "non_living_area": 0.0,
                "odi_delta": 0.0, "house_url": "",
                "found_ogrn": "", "is_mismatch": False,
                "found_fio": "", "found_fio_ogrn": "", "found_fio_inn": "",
                "is_mismatch_fio": False
            })

    best = {}
    for r in records:
        key = ((r["org"] or "").strip().lower(), (r["addr"] or "").strip().lower())
        cur = best.get(key)
        if cur is None or r["_mtime"] > cur["_mtime"]: best[key] = r
    result = []
    for r in records:
        key = ((r["org"] or "").strip().lower(), (r["addr"] or "").strip().lower())
        if best.get(key) is r:
            r.pop("_mtime", None)
            result.append(r)
    return result

# ============================== GISJHK ==============================
RE_HOUSE_INFO = re.compile(
    r"house_info:\s+"
    r"(?=.*?\borg='([^']*)')"
    r"(?=.*?\baddr='([^']*)')"
    r"(?=.*?\bpdf='([^']*)')"
    r"(?=.*?\bdate='([^']*)')"
)
RE_RESULT_MARKER = re.compile(r"result_text:\s*'([^']*)'\s+marker:\s*'([^']*)'")
RE_RESULT_NONE = re.compile(r"result_text:\s*'ДЕЙСТВИТЕЛЬНА'\s+marker:\s*None")

def _iter_gisjkh_files():
    return sorted(LOG_DIR.glob("gisjkh-ufo*.log"), key=lambda p: p.name)

def parse_gisjkh_logs():
    stats = {}
    for path in _iter_gisjkh_files():
        current_org = None
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m_hi = RE_HOUSE_INFO.search(line)
                if m_hi:
                    current_org = m_hi.group(1).strip().upper()
                    continue
                m_res = RE_RESULT_MARKER.search(line)
                if m_res and current_org:
                    marker = m_res.group(2).strip()
                    if marker in ("Н", "Ж"): _bump(stats, current_org, marker)
                    current_org = None
                    continue
                if RE_RESULT_NONE.search(line) and current_org:
                    _bump(stats, current_org, "Д")
                    current_org = None
                    continue
    return stats

def _bump(stats: dict, org: str, marker: str):
    if org not in stats: stats[org] = {"total": 0, "Д": 0, "Н": 0, "Ж": 0}
    stats[org]["total"] += 1
    stats[org][marker] = stats[org].get(marker, 0) + 1

# ============================== АНАЛИТИКА ==============================
def compute_mass_date_per_org(records):
    counts_per_org = {}
    for r in records:
        org = r["org"]
        date = (r["date"] or "").strip()
        if not RE_DATE_DDMMYYYY.match(date): continue
        if org not in counts_per_org: counts_per_org[org] = {}
        counts_per_org[org][date] = counts_per_org[org].get(date, 0) + 1

    result = {}
    for org, counts in counts_per_org.items():
        mass = {d: c for d, c in counts.items() if c >= MASS_DATE_MIN}
        if mass: result[org] = mass
    return result

def _norm_org(s: str) -> str:
    if not s: return ""
    s = s.upper().strip().replace("«", '"').replace("»", '"').replace("<", '"').replace(">", '"')
    return re.sub(r"\s+", " ", s)

def _norm_addr(s: str) -> str: return (s or "").strip()

def _match_d_per_org(records, org_stats):
    org_stats_norm = {_norm_org(raw_org): st for raw_org, st in org_stats.items()}
    addr_to_orgs = {}
    for r in records:
        addr_to_orgs.setdefault(_norm_addr(r["addr"]), set()).add(_norm_org(r["org"]))
    result = {}
    for r in records:
        raw_org, o_norm, a_norm = r["org"], _norm_org(r["org"]), _norm_addr(r["addr"])
        st = org_stats_norm.get(o_norm)
        d_here = st.get("Д", 0) if st else 0
        if d_here == 0:
            orgs = addr_to_orgs.get(a_norm, set())
            if len(orgs) == 1:
                st2 = org_stats_norm.get(next(iter(orgs)))
                if st2: d_here = st2.get("Д", 0)
        result[raw_org] = max(result.get(raw_org, 0), d_here)
    return result

def compute_org_summary(records, org_stats, mass_per_org, egrul_directors, egrul_full_all):
    summary = {}
    for r in records:
        org = r["org"]
        if org not in summary:
            summary[org] = {
                "org": org, "addr_count": 0, "n_count": 0, "j_count": 0,
                "e_count": 0, "o_count": 0, "mass_date": "", "mass_count": 0,
                "mass_dates": {}, "trust": 0.0, "holiday_count": 0,
                "ogrns": [], "director": "", "capital": "",
                "odi_neg_count": 0, "odi_neg_sum": 0.0
            }
        s = summary[org]
        s["addr_count"] += 1
        if r["marker"] == "Н": s["n_count"] += 1
        elif r["marker"] == "Ж": s["j_count"] += 1
        elif r["marker"] == "Э": s["e_count"] += 1
        elif r["marker"] == "О": s["o_count"] += 1

        ogrn = r.get("ogrn") or ""
        if ogrn and ogrn not in s["ogrns"]: s["ogrns"].append(ogrn)

        odi = r.get("odi_delta", 0.0)
        if odi < -0.01:
            s["odi_neg_count"] += 1
            s["odi_neg_sum"] += odi

        if r.get("is_holiday"): s["holiday_count"] += 1

    d_per_org = _match_d_per_org(records, org_stats)
    for org, s in summary.items():
        d = d_per_org.get(org, 0)
        denominator = d + s["n_count"] + s["j_count"] + s["e_count"] + s["o_count"]
        if denominator > 0: s["trust"] = round(d / denominator * 100, 1)
        else: s["trust"] = 0.0

        mass = mass_per_org.get(org)
        if mass:
            max_date = max(mass.items(), key=lambda kv: kv[1])
            s["mass_date"] = max_date[0]
            s["mass_count"] = max_date[1]
            s["mass_dates"] = mass

        s["ogrns"] = sorted(s["ogrns"])[:1]
        if s["ogrns"]:
            ogrn = s["ogrns"][0]
            s["director"] = egrul_directors.get(ogrn, "")
            if ogrn in egrul_full_all:
                s["capital"] = egrul_full_all[ogrn].get("capital", "")
    return summary

# ============================== HTML-ШАБЛОН ==============================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<body>
<div class="epigraph">
  <div class="epigraph-title">Правовая рамка исследования</div>
  <p>Согласно <b>п. 8 ст. 4 ФЗ № 209-ФЗ</b>, создание и эксплуатация ГИС ЖКХ осуществляются на основе принципа <i>«полноты, достоверности, актуальности информации и своевременности её размещения»</i>. Согласно <b>п. 4 ст. 5</b>, система должна обеспечивать возможность получения <i>«достоверной и актуальной информации»</i>.</p>
  <p><b>Ч. 9 ст. 14 ФЗ № 149-ФЗ</b> («Об информации…») прямо устанавливает:</p>
  <blockquote>«Информация, содержащаяся в государственных информационных системах, является <b>официальной</b>. Государственные органы… <b>обязаны обеспечить достоверность и актуальность</b> информации, содержащейся в данной информационной системе…»</blockquote>
</div>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         margin: 20px; background: #f5f5f5; color: #222; }
    h1 { font-size: 20px; margin-bottom: 10px; }
  .epigraph {
      text-align: right;
      max-width: 560px;
      margin: 0 0 18px auto;
      padding: 10px 14px;
      border-right: 3px solid #ccc;
      color: #444;
      font-size: 12px;
      line-height: 1.5;
  }
  .epigraph-title {
      font-weight: 700;
      font-size: 13px;
      color: #222;
      margin-bottom: 6px;
  }
  .epigraph p { margin: 0 0 8px 0; }
  .epigraph blockquote {
      margin: 0;
      padding: 0 0 0 12px;
      border-left: 2px solid #ddd;
      font-style: italic;
      color: #333;
  }
  .meta { color: #666; font-size: 13px; margin-bottom: 16px; line-height: 1.5; }
  .legal { color: #444; font-size: 12px; margin-top: 24px; line-height: 1.55;
           border-top: 1px solid #ddd; padding-top: 12px; }
  .legal h2 { font-size: 14px; margin: 0 0 8px 0; }
  .legal ul { margin: 0 0 8px 0; padding-left: 20px; }
  table { border-collapse: collapse; background: #fff; width: 100%;
          box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
  th:nth-child(1) { width: 28px; }
  th:nth-child(2) { min-width: 320px; }
  th:nth-child(3) { min-width: 280px; }
  th:nth-child(6) { width: 220px; }
  th, td { border: 1px solid #ddd; padding: 6px 8px; font-size: 13px;
           vertical-align: top; word-wrap: break-word; overflow-wrap: anywhere; }
  th { background: #eaeaea; cursor: pointer; user-select: none;
       position: sticky; top: 0; }
  th:hover { background: #ddd; }
  th .arrow { color: #999; font-size: 11px; margin-left: 4px; }
  th.no-sort { cursor: default; }
  th.no-sort:hover { background: #eaeaea; }
  th.col-toggle { width: 28px; cursor: default; }
  th.col-toggle:hover { background: #eaeaea; }

  tr.org-row td { background: #f3f3f3; font-weight: 600; }
  tr.org-row:hover td { background: #ececec; }
  tr.org-row td.org-cell { white-space: normal; }

  td.org-cell .org-name { display: block; text-align: left; font-weight: 600; cursor: help; }
  td.org-cell .org-ogrn {
      display: block; text-align: left; font-weight: 400; color: #555;
      font-size: 11px; font-variant-numeric: tabular-nums; margin-top: 2px;
  }
  td.org-cell .org-director { cursor: help; }
  td.org-cell .org-score {
      display: block; text-align: right; font-weight: 700;
      font-variant-numeric: tabular-nums;
  }
  td.toggle-cell {
      text-align: center; vertical-align: middle; cursor: pointer;
      user-select: none; font-weight: bold; font-size: 16px; width: 28px;
  }
  td.toggle-cell:hover { background: #dcdcdc; }

  td.mass-date { font-weight: 600; }
  td.mass-date .mass-date-text { cursor: help; }

  td.holiday-date { color: #b00; font-weight: bold; }
  td.marker-zh  { color: #b00; font-weight: bold; }
  td.marker-n   { color: #d68000; font-weight: bold; }
  td.marker-other { color: #333; font-weight: bold; }
  .n-counter { color: #d68000; font-weight: bold; }
  .j-counter { color: #b00; font-weight: bold; margin-left: 14px; }
  .e-counter { color: #333; font-weight: bold; margin-left: 14px; }
  .o-counter { color: #333; font-weight: bold; margin-left: 14px; }

  .area-total { color: #0b5cad; font-weight: bold; }
  .area-living { color: #147a29; font-weight: bold; }
  .area-nonliving { color: #d68000; font-weight: bold; }
  .area-result { color: #b00; font-weight: bold; }

  sub { font-size: 85%; }

  a.addr-link { color: inherit; text-decoration: none; display: inline-block; }
  a.addr-link:hover { color: #0b5cad; text-decoration: underline; }

  td.addr-cell {
      display: flex; justify-content: space-between;
      align-items: flex-start; gap: 12px;
  }
  td.addr-cell .addr-text { flex: 1 1 auto; }
  td.addr-cell .addr-delta {
      flex: 0 0 auto; font-weight: bold;
      font-variant-numeric: tabular-nums; white-space: nowrap;
      text-align: right;
  }

  tr.house-row { display: none; }
  tr.house-row.visible { display: table-row; }
  tr.house-row td.house-addr { padding-left: 32px; }
  .btn-view {
    display: inline-block; padding: 4px 10px; cursor: pointer;
    border: 1px solid #888; border-radius: 4px; background: #f0f0f0;
    font-size: 12px; user-select: none;
  }
  .btn-view:hover { background: #e0e0e0; }
  #overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.92);
    display: none; z-index: 9999; align-items: center; justify-content: center;
  }
  #overlay.open { display: flex; }
  #overlay .frame {
    position: relative; max-width: 95vw; max-height: 95vh;
    width: 95vw; height: 95vh; display: flex; align-items: center; justify-content: center;
  }
  #overlay img {
    max-width: 100%; max-height: 100%; position: absolute; opacity: 0;
    transition: opacity 0.25s; object-fit: contain; user-select: none;
  }
  #overlay img.active { opacity: 1; }
  #overlay .close {
    position: absolute; top: 12px; right: 16px; color: #fff; font-size: 28px;
    cursor: pointer; background: rgba(0,0,0,0.4); border-radius: 50%;
    width: 40px; height: 40px; line-height: 36px; text-align: center;
    user-select: none;
  }
  #overlay .close:hover { background: rgba(255,255,255,0.2); }
  #overlay .caption {
    position: absolute; left: 16px; bottom: 12px; color: #ddd; font-size: 13px;
    font-family: monospace; background: rgba(0,0,0,0.5); padding: 6px 10px; border-radius: 4px;
  }
  #overlay .caption #ov_filename {
    display: block; color: #fff; font-weight: bold; margin-top: 4px;
  }

  #hint-tooltip {
    position: fixed; display: none; z-index: 10000;
    background: #ffffff; color: #333; border: 1px solid #ccc;
    border-radius: 4px; padding: 8px 10px; font-size: 11px;
    line-height: 1.4; max-width: 350px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    pointer-events: none;
  }

  tr.total-row td {
      background: #fafafa;
      border-top: 2px solid #888;
      font-weight: normal;
  }
</style>
</head>
<body>
<h1>Отчёт по проверке квалифицированной Электронной Подписи договоров Управляющих Компаний Омской области</h1>
<div style="margin-bottom:8px;">
  <button id="btn-expand-all" style="padding: 6px 14px; cursor: pointer; border: 1px solid #888; border-radius: 4px; background: #f0f0f0; font-size: 13px;">Развернуть все</button>
</div>
<div class="meta">
  Записей: <span id="count">0</span>.<br>
  <b>Уровень доверия</b> по УК = Д / (Д + Н/Д + Н/В + Н/Э + Н/О) × 100%. Цвет — по тепловой шкале (голубой = высокий, бордовый = низкий). Сортировка по умолчанию — по уровню доверия <b>↑</b> (аутсайдеры сверху). В отчёт не попадает только Д (подпись ДЕЙСТВИТЕЛЬНА) — Д идёт исключительно в статистику уровня доверия.<br>
  <b>Уставный капитал</b> указан рядом с ОГРН и окрашен по тепловой шкале (от бордового &lt; 10 000 руб до голубого &gt; 1 000 000 руб).<br>
  <b>Массовые одинаковые даты заключения договоров</b> (у одной УК, ≥<span id="mass_min">2</span> раз): в свёрнутой строке УК показана дата с максимальным числом повторов, в раскрытых строках домов пастелью подсвечены <b>все</b> такие даты.<br>
  <b>Адрес МКД</b> является также ссылкой на Информацию об управлении МКД на портале ГИС ЖКХ.<br>
  <b>Формула исчисления площадей в МКД:</b> <span class="area-result">S<sub>оди</sub> = </span> <span class="area-total">S<sub>общ</sub></span> − <span class="area-living">S<sub>жил</sub></span> − <span class="area-nonliving">S<sub>нежил</sub></span> (ст. 15 ЖК РФ). Если результат <b>отрицательный</b>, справа от адреса выводится формула с подставленными из ГИС ЖКХ цифрами.<br>
  <b>Расшифровка маркеров столбца «Результат проверки ЭП»:</b> <b>Н/Д</b> — подпись верна, но НЕДЕЙСТВИТЕЛЬНА; <b>Н/В</b> — подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА; <b>Н/Э</b> — информация ПРОТИВОРЕЧИВА или нет ЭП; <b>Н/О</b> — информация о договоре НЕ РАЗМЕЩЕНА.<br>
  <b>В последнем столбце</b>: если из скана удалось распознать иное ЮЛ или ФЛ — оно выводится рядом с кнопкой «Смотреть». Также имеет предположительный граф аффилированности.<br>
  <b>Предположительный граф аффилированности</b> показывается подсказкой при наведении курсора на ЮЛ или ФИО в первом и последнем столбцах.
</div>

<table id="tbl">
  <thead>
    <tr>
      <th class="col-toggle"></th>
      <th data-key="trust">Организация по уровню доверия (ОГРН; уст. капитал; Руководитель)<span class="arrow"></span></th>
      <th data-key="addr">Адрес (расчёт площадей)<span class="arrow"></span></th>
      <th data-key="date">Дата заключения ДУ с УК<span class="arrow"></span></th>
      <th data-key="marker">Результат проверки ЭП<span class="arrow"></span></th>
      <th class="no-sort">Протокол проверки (иное ЮЛ или ФЛ)</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div style="margin-top:16px;">
  <a id="btn-download-errors" href="download_errors.html" target="_blank" rel="noopener" style="display:inline-block; padding:6px 14px; border:1px solid #888; border-radius:4px; background:#f0f0f0; font-size:13px; text-decoration:none; color:#222;">Открыть лог ошибок</a>
</div>

<div class="legal">
  <h2>Отказ от ответственности.</h2>
    <p>Отчёт сформирован автоматически по результатам программного опроса открытых источников — порталов ГИС ЖКХ и ЕГРЮЛ (<a href="https://my.dom.gosuslugi.ru" target="_blank" rel="noopener">https://my.dom.gosuslugi.ru</a> и <a href="https://e-trust.gosuslugi.ru" target="_blank" rel="noopener">https://e-trust.gosuslugi.ru</a>). Логика отчёта выстроена с помощью искусственного интеллекта. Отчёт не содержит утверждений о виновности конкретных лиц; он демонстрирует методику выявления связей на основе открытых данных. Выводы являются интерпретацией и не заменяют собой решение уполномоченного органа.</p>

  <h2>Перечень нормативных правовых актов</h2>
  <ul>
    <li>Конституция Российской Федерации.</li>
    <li>Жилищный кодекс Российской Федерации.</li>
    <li>Гражданский кодекс Российской Федерации (ст. 160, 432, 388, 391, 1102).</li>
    <li>Федеральный закон от 21.07.2014 № 209-ФЗ «О государственной информационной системе жилищно-коммунального хозяйства» (п. 8 ст. 4, п. 4 ст. 5).</li>
    <li>Федеральный закон от 27.07.2006 № 149-ФЗ «Об информации, информационных технологиях и о защите информации» (ч. 9 ст. 14).</li>
    <li>Федеральный закон от 06.04.2011 № 63-ФЗ «Об электронной подписи».</li>
    <li>Кодекс Российской Федерации об административных правонарушениях (ст. 20.4).</li>
    <li>Уголовный кодекс Российской Федерации (ст. 159, 159.2, 272, 327).</li>
    <li>Постановление Правительства РФ от 13.08.2006 № 491 «Об утверждении Правил содержания общего имущества в многоквартирном доме…».</li>
    <li>Постановление Правительства РФ от 06.05.2011 № 354 «О предоставлении коммунальных услуг собственникам и пользователям помещений…».</li>
  </ul>
</div>

<div id="overlay">
  <div class="frame" id="ov_frame">
    <div class="close" onclick="closeOverlay()">×</div>
    <img id="ov_img0" alt=""><img id="ov_img1" alt=""><img id="ov_img2" alt=""><img id="ov_img3" alt=""><img id="ov_img4" alt="">
    <div class="caption" id="ov_caption">
        Для паузы нажми Пробел или тапни по экрану
        <span id="ov_filename"></span>
    </div>
  </div>
</div>

<div id="hint-tooltip"></div>
<div id="tip" style="position:fixed; z-index:10000; background:#fff; border:1px solid #c8c8c8; border-radius:5px; padding:6px 8px; font-family:Consolas,monospace; font-size:7pt; line-height:1.3; max-width:95vw; box-shadow:0 6px 22px rgba(0,0,0,0.18); pointer-events:none; display:none; overflow:hidden;"></div>

<script>
const DATA = __DATA__;
const ORG_STATS_BACKEND = __ORG_STATS__;
const ORG_SUMMARY_BACKEND = __ORG_SUMMARY__;
const EGRUL_ENTITIES = __EGRUL_ENTITIES__;
const EGRUL_LOST = __EGRUL_LOST__;
const EGRUL_FULL = __EGRUL_FULL__;
const GRAPHS = __GRAPHS__;
const MASS_DATE_MIN = __MASS_DATE_MIN__;
const MASS_DATE_HINT = __MASS_DATE_HINT__;

const orgByInn = new Map();
for (const e of Object.values(EGRUL_ENTITIES)) {
  if (e.ul_inn) orgByInn.set(e.ul_inn, e);
}
for (const e of Object.values(EGRUL_LOST)) {
  if (e.ul_inn && !orgByInn.has(e.ul_inn)) orgByInn.set(e.ul_inn, e);
}

const tipEl = document.getElementById("tip");

function renderTipHtml(lines) {
  const out = [];
  for (const ln of lines) {
    if (ln.type === "title") out.push('<div style="color:#0b5cad; font-weight:700; margin-bottom:3px; font-family:sans-serif; font-size:7pt;">' + esc(ln.text) + '</div>');
    else if (ln.type === "sect") out.push('<div style="color:#666; font-weight:600; margin:3px 0 1px 0; font-size:7pt;">' + esc(ln.text) + '</div>');
    else if (ln.type === "who") out.push('<div style="color:#444; white-space:pre; font-size:7pt;">' + esc(ln.text) + '</div>');
    else out.push('<div style="color:#222; white-space:pre; font-size:7pt;">' + esc(ln.text) + '</div>');
  }
  return out.join("");
}

function positionTip(x, y) {
  const rect = tipEl.getBoundingClientRect();
  const pad = 12;
  let left = x + 16, top = y + 16;
  if (left + rect.width + pad > window.innerWidth) left = x - rect.width - 16;
  if (top + rect.height + pad > window.innerHeight) top = y - rect.height - 16;
  if (left < pad) left = pad;
  if (top < pad) top = pad;
  tipEl.style.left = left + "px";
  tipEl.style.top = top + "px";
}

function showTipFor(el, x, y) {
  let lines = null;

  if (el.classList.contains("org-director")) {
    const inn = el.dataset.inn || "";
    const fio = (el.dataset.fio || "").toUpperCase();
    if (inn && GRAPHS.by_fio[inn]) {
      lines = GRAPHS.by_fio[inn];
    } else if (fio && GRAPHS.by_fio[fio]) {
      lines = GRAPHS.by_fio[fio];
    } else {
      return;
    }
  } else if (el.classList.contains("org-name")) {
    const ogrn = el.dataset.ogrn || "";
    if (GRAPHS.by_org[ogrn]) {
      lines = GRAPHS.by_org[ogrn];
    } else {
      return;
    }
  } else return;

  tipEl.innerHTML = renderTipHtml(lines);
  tipEl.style.display = "block";
  positionTip(x, y);
}

function hideTip() { tipEl.style.display = "none"; }

document.addEventListener("mouseover", (ev) => {
  const el = ev.target.closest(".org-name, .org-director");
  if (el) showTipFor(el, ev.clientX, ev.clientY);
});
document.addEventListener("mousemove", (ev) => {
  const el = ev.target.closest(".org-name, .org-director");
  if (!el) { hideTip(); return; }
  if (tipEl.style.display === "block") positionTip(ev.clientX, ev.clientY);
});
document.addEventListener("mouseout", (ev) => {
  const el = ev.target.closest(".org-name, .org-director");
  if (!el) return;
  const to = ev.relatedTarget;
  if (!to || !to.closest || !to.closest(".org-name, .org-director")) hideTip();
});

let sortKey = "trust", sortAsc = true, slideshowTimer = null;
const FRAME_MS = 2000;
let slideshowState = { imgs: [], srcs: [], cur: 0, paused: false, activeCount: 0 };
const expandedOrgs = new Set();

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}
function fmtArea(v) {
  if (v === null || v === undefined) return "0";
  var s = v.toFixed(2);
  if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s;
}
function fmtOdiDelta(v) {
  if (v === null || v === undefined || Math.abs(v) < 0.01) return "";
  var s = Math.abs(v).toFixed(2);
  if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return "\u2212" + s + " м<sup>2</sup>";
}
function trustColor(trust) {
  if (trust >= 99.5) return "hsl(200, 70%, 50%)";
  if (trust >= 81)   return "hsl(140, 60%, 40%)";
  if (trust >= 61)   return "hsl(80, 65%, 40%)";
  if (trust >= 41)   return "hsl(50, 80%, 40%)";
  if (trust >= 21)   return "hsl(30, 85%, 45%)";
  if (trust >= 0.5)  return "hsl(10, 80%, 45%)";
  return "hsl(350, 70%, 35%)";
}
function fmtCapital(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  if (!isFinite(n)) return "";
  return n.toLocaleString("ru-RU").replace(/,/g, " ");
}
function capitalColor(v) {
  const n = Number(v);
  if (!isFinite(n) || n <= 0) return "#888";
  if (n >= 1000000) return "hsl(200, 70%, 50%)";
  if (n >= 500000)  return "hsl(140, 60%, 40%)";
  if (n >= 100000)  return "hsl(80, 65%, 40%)";
  if (n >= 50000)   return "hsl(50, 80%, 40%)";
  if (n >= 20000)   return "hsl(30, 85%, 45%)";
  if (n >= 10000)   return "hsl(10, 80%, 45%)";
  return "hsl(350, 70%, 35%)";
}
function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function pastelColorForDate(dateStr) { return "hsl(" + (hashCode(dateStr) % 360) + ", 50%, 92%)"; }

function showHint(e) {
  const el = document.getElementById("hint-tooltip");
  el.textContent = MASS_DATE_HINT;
  el.style.display = "block";
  let x = e.clientX + 15, y = e.clientY + 15;
  const rect = el.getBoundingClientRect();
  if (x + rect.width > window.innerWidth) x = e.clientX - rect.width - 15;
  if (y + rect.height > window.innerHeight) y = e.clientY - rect.height - 15;
  el.style.left = x + "px";
  el.style.top = y + "px";
}
function hideHint() { document.getElementById("hint-tooltip").style.display = "none"; }

function render() {
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";

  const byOrg = {};
  DATA.forEach(r => {
    if (!byOrg[r.org]) byOrg[r.org] = [];
    byOrg[r.org].push(r);
  });

  const orgs = Object.keys(byOrg);

  orgs.sort((a, b) => {
    const ta = ORG_SUMMARY_BACKEND[a] ? ORG_SUMMARY_BACKEND[a].trust : 0;
    const tb = ORG_SUMMARY_BACKEND[b] ? ORG_SUMMARY_BACKEND[b].trust : 0;
    if (ta !== tb) return sortAsc ? ta - tb : tb - ta;
    const cmp = a.toLowerCase().localeCompare(b.toLowerCase());
    return sortAsc ? cmp : -cmp;
  });

  let globalNegSum = 0;

  orgs.forEach(org => {
    const s = ORG_SUMMARY_BACKEND[org] || {
      trust: 0, addr_count: 0, n_count: 0, j_count: 0,
      e_count: 0, o_count: 0, mass_date: "", mass_count: 0,
      mass_dates: {}, holiday_count: 0, ogrns: [], director: ""
    };
    const color = trustColor(s.trust);

    const tr = document.createElement("tr");
    tr.className = "org-row";

    let dateCellHtml = "";
    if (s.mass_date) {
      const bg = pastelColorForDate(s.mass_date);
      dateCellHtml = "<td class='mass-date' style='background:" + bg + "'>" +
        "<span class='mass-date-text'>" + esc(s.mass_date) + " <b>(" + s.mass_count + "×)</b></span>" +
        "</td>";
    } else dateCellHtml = "<td></td>";

    let counters = [];
    if (s.n_count > 0) counters.push("<span class='n-counter'>Н/Д: " + s.n_count + "</span>");
    if (s.j_count > 0) counters.push("<span class='j-counter'>Н/В: " + s.j_count + "</span>");
    if (s.e_count > 0) counters.push("<span class='e-counter'>Н/Э: " + s.e_count + "</span>");
    if (s.o_count > 0) counters.push("<span class='o-counter'>Н/О: " + s.o_count + "</span>");
    const markerCellHtml = "<td>" + counters.join("") + "</td>";

    let addrCellHtml = "<td>" + s.addr_count + "</td>";

    if (s.odi_neg_sum < -0.01) {
        globalNegSum += s.odi_neg_sum;
        let deltaHtml = "<span class='area-result'>" + fmtOdiDelta(s.odi_neg_sum) + "</span>";
        addrCellHtml = "<td class='addr-cell'><span class='addr-text'>" + s.addr_count + "</span><span class='addr-delta'>" + deltaHtml + "</span></td>";
    }

    let orgOgrn = (s.ogrns && s.ogrns.length > 0) ? s.ogrns[0] : "";
    let egrul = EGRUL_ENTITIES[orgOgrn] || null;
    let dirFio = egrul ? (egrul.director || "").toUpperCase() : "";
    let dirInn = egrul ? (egrul.director_inn || "") : "";

    let capHtml = "";
    if (s.capital != null && s.capital !== "") {
      const capFmt = fmtCapital(s.capital);
      const capColor = capitalColor(s.capital);
      if (capFmt) {
        capHtml = " - <span style='color:" + capColor + "; font-weight:bold;'>" + capFmt + " руб</span>";
      }
    }

    let orgNameHtml = "<span class='org-name'" +
        (orgOgrn ? " data-ogrn='" + esc(orgOgrn) + "'" : "") +
        ">" + esc(org) + "</span>";
    let ogrnHtml = "";
    if (orgOgrn) {
        ogrnHtml = "<span class='org-ogrn'>ОГРН: " + esc(orgOgrn) + capHtml + "</span>";
    }
    let dirHtml = "";
    if (dirFio) {
        if (dirInn) {
            dirHtml = "<span class='org-director' data-inn='" + esc(dirInn) + "' data-fio='" + esc(dirFio) + "'>" + esc(dirFio) + "</span>";
        } else {
            dirHtml = "<span class='org-director' data-fio='" + esc(dirFio) + "'>" + esc(dirFio) + "</span>";
        }
    }

    tr.innerHTML =
      "<td class='toggle-cell'>+</td>" +
      "<td class='org-cell'>" +
        orgNameHtml +
        ogrnHtml +
        dirHtml +
        "<span class='org-score' style='color:" + color + "'>" + s.trust.toFixed(0) + "%</span>" +
      "</td>" +
      addrCellHtml + dateCellHtml + markerCellHtml + "<td></td>";

    tbody.appendChild(tr);

    if (s.mass_date) {
      const dateSpan = tr.querySelector(".mass-date-text");
      if (dateSpan) {
        dateSpan.addEventListener("mouseenter", showHint);
        dateSpan.addEventListener("mousemove", showHint);
        dateSpan.addEventListener("mouseleave", hideHint);
      }
    }

    const isOpen = expandedOrgs.has(org);
    const houseRows = [];

    const houseList = byOrg[org].slice();
    houseList.sort((a, b) => {
      let va, vb;
      if (sortKey === "trust") { va = (a.date || "").toLowerCase(); vb = (b.date || "").toLowerCase(); }
      else if (sortKey === "marker") {
        va = (a.marker || "").toLowerCase(); vb = (b.marker || "").toLowerCase();
        if (va === vb) { va = (a.date || "").toLowerCase(); vb = (b.date || "").toLowerCase(); }
      } else {
        va = (a[sortKey] ?? "").toString().toLowerCase(); vb = (b[sortKey] ?? "").toString().toLowerCase();
      }
      if (va < vb) return sortAsc ? -1 : 1;
      if (va > vb) return sortAsc ? 1 : -1;
      return 0;
    });

    houseList.forEach(r => {
      const markerCls = r.marker === "Ж" ? "marker-zh" : r.marker === "Н" ? "marker-n" : "marker-other";
      const mass = !!(s.mass_dates && s.mass_dates[r.date]);

      const dateClasses = [];
      if (mass) dateClasses.push("mass-date");
      if (r.is_holiday) dateClasses.push("holiday-date");
      const dateClass = dateClasses.length ? " class='" + dateClasses.join(" ") + "'" : "";
      const dateStyle = mass ? " style='background:" + pastelColorForDate(r.date) + "'" : "";

      let addrCell;
      const odi = r.odi_delta;

      let addrHtml = esc(r.addr);
      if (r.house_url) {
          addrHtml = "<a href='" + esc(r.house_url) + "' target='_blank' class='addr-link'>" + esc(r.addr) + "</a>";
      }

      if (odi !== null && odi !== undefined && odi < -0.01) {
        const formula =
            "<span class='area-total'>" + fmtArea(r.total_area) + "</span> - " +
            "<span class='area-living'>" + fmtArea(r.living_area) + "</span> - " +
            "<span class='area-nonliving'>" + fmtArea(r.non_living_area) + "</span> = " +
            "<span class='area-result'>" + fmtOdiDelta(odi) + ". Это S<sub>оди</sub>?</span>";
        addrCell = "<td class='addr-cell house-addr'><span class='addr-text'>" + addrHtml + "</span><span class='addr-delta'>" + formula + "</span></td>";
      } else {
        addrCell = "<td class='house-addr'>" + addrHtml + "</td>";
      }

      const viewHtml = r.has_real_screens ? "<span class='btn-view'>Смотреть</span>" : "";

      let mismatchHtml = "";
      if (r.is_mismatch && r.found_ogrn) {
          let lostInfo = null;
          if (r.found_ogrn.length === 13) {
              lostInfo = EGRUL_LOST[r.found_ogrn] || EGRUL_ENTITIES[r.found_ogrn];
          } else if (r.found_ogrn.length === 10) {
              lostInfo = orgByInn.get(r.found_ogrn);
          }

          if (r.found_ogrn.length === 13) {
              const full = EGRUL_FULL[r.found_ogrn];
              if (full) {
                  if (!lostInfo) lostInfo = {};
                  lostInfo = Object.assign({}, full, lostInfo, {
                      director: (lostInfo.director || full.director || "").toUpperCase(),
                      director_inn: lostInfo.director_inn || full.director_inn || "",
                      name: lostInfo.name || full.name || "",
                      termination_date: lostInfo.termination_date || full.termination_date || ""
                  });
              }
          }

          if (lostInfo) {
              let orgName = (lostInfo.name || "").toUpperCase();
              let orgOgrn = lostInfo.ogrn || r.found_ogrn;
              let orgDir  = (lostInfo.director || "").toUpperCase();
              let termDate = lostInfo.termination_date ? " (Прекращена: " + lostInfo.termination_date + ")" : "";
              let lostDirInn = lostInfo.director_inn || "";

              let lines = [];
              if (orgName) lines.push("<span class='org-name' data-lost='1' data-ogrn='" + esc(orgOgrn) + "'>" + esc(orgName) + "</span>");
              lines.push("ОГРН: " + esc(orgOgrn));
              if (orgDir) {
                  if (lostDirInn) {
                      lines.push("<span class='org-director' data-lost='1' data-inn='" + esc(lostDirInn) + "' data-fio='" + esc(orgDir) + "'>" + esc(orgDir) + "</span>");
                  } else {
                      lines.push("<span class='org-director' data-lost='1' data-fio='" + esc(orgDir) + "'>" + esc(orgDir) + "</span>");
                  }
              }
              if (termDate) lines.push(esc(termDate));

              mismatchHtml = "<div style='color:#000; font-size:7pt; margin-left:10px; display:inline-block; vertical-align:top;'>" + lines.join("<br>") + "</div>";
          }
      } else if (r.is_mismatch_fio && r.found_fio) {
          let fio = (r.found_fio || "").toUpperCase();
          let fioInn = r.found_fio_inn || "";
          let lines = [];
          if (fioInn) {
              lines.push("<span class='org-director' data-lost='1' data-inn='" + esc(fioInn) + "' data-fio='" + esc(fio) + "'>" + esc(fio) + "</span>");
          } else {
              lines.push("<span class='org-director' data-lost='1' data-fio='" + esc(fio) + "'>" + esc(fio) + "</span>");
          }
          mismatchHtml = "<div style='color:#000; font-size:7pt; margin-left:10px; display:inline-block; vertical-align:top;'>" + lines.join("<br>") + "</div>";
      }

      const htr = document.createElement("tr");
      htr.className = "house-row" + (isOpen ? " visible" : "");
      htr.innerHTML =
        "<td></td>" +
        "<td></td>" +
        addrCell +
        "<td" + dateClass + dateStyle + ">" + esc(r.date) + "</td>" +
        "<td class='" + markerCls + "'>" + esc(r.marker_long) + "</td>" +
        "<td>" + viewHtml + mismatchHtml + "</td>";

      const btn = htr.querySelector(".btn-view");
      if (btn) {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          openOverlay(r);
        });
      }

      tbody.appendChild(htr);
      houseRows.push(htr);
    });

    const toggle = tr.querySelector(".toggle-cell");
    toggle.textContent = isOpen ? "−" : "+";
    toggle.addEventListener("click", () => {
      const nowOpen = !expandedOrgs.has(org);
      if (nowOpen) expandedOrgs.add(org);
      else expandedOrgs.delete(org);
      houseRows.forEach(hr => {
        if (nowOpen) hr.classList.add("visible");
        else hr.classList.remove("visible");
      });
      toggle.textContent = nowOpen ? "−" : "+";
    });
  });

  if (globalNegSum < -0.01) {
    const totalAbs = Math.abs(globalNegSum);
    const flats = Math.round(totalAbs / 50);
    const costMin = Math.round(totalAbs * 10);
    const costMax = Math.round(totalAbs * 18);

    const trTotal = document.createElement("tr");
    trTotal.className = "total-row";
    trTotal.innerHTML =
      "<td></td>" +
      "<td><b>ИТОГО по всем УК</b></td>" +
      "<td class='addr-cell'><span class='addr-text'>Сумма отрицательных площадей:</span><span class='addr-delta area-result'>" + fmtOdiDelta(globalNegSum) + "</span></td>" +
      "<td colspan='3' style='color:#666; font-size:12px; line-height: 1.4;'>" +
        "Это площадь в среднем <b>" + flats.toLocaleString('ru-RU') + " квартир</b>." +
      "</td>";
    tbody.appendChild(trTotal);
  }

  document.getElementById("count").textContent = DATA.length;
}

function openOverlay(record) {
  const ov = document.getElementById("overlay");
  const imgs = [0,1,2,3,4].map(i => document.getElementById("ov_img" + i));
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  const srcs = (record.screens && record.screens.length) ? record.screens.slice() : [record.scr_a, record.scr_b, record.scr_c].filter(Boolean).concat(record.scr_pdf ? [record.scr_pdf] : []);

  imgs.forEach(im => { im.classList.remove("active"); im.removeAttribute("src"); im.style.display = "none"; });
  srcs.forEach((src, i) => { if (i >= imgs.length) return; imgs[i].src = src; imgs[i].style.display = ""; });
  imgs[0].classList.add("active");

  slideshowState.imgs = imgs;
  slideshowState.srcs = srcs;
  slideshowState.cur = 0;
  slideshowState.paused = false;
  slideshowState.activeCount = Math.min(srcs.length, imgs.length);

  ov.classList.remove("paused");

  document.getElementById("ov_filename").textContent = srcs[0] || "";
  ov.classList.add("open");
  startSlideshowTimer();
}
function startSlideshowTimer() {
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  const n = slideshowState.activeCount;
  if (n <= 0) return;
  slideshowTimer = setInterval(() => {
    if (slideshowState.paused) return;
    if (slideshowState.cur >= n - 1) { clearInterval(slideshowTimer); slideshowTimer = null; closeOverlay(); return; }

    slideshowState.imgs[slideshowState.cur].classList.remove("active");
    slideshowState.cur += 1;
    slideshowState.imgs[slideshowState.cur].classList.add("active");

    const fn = document.getElementById("ov_filename");
    if (slideshowState.srcs && slideshowState.srcs[slideshowState.cur]) {
        fn.textContent = slideshowState.srcs[slideshowState.cur];
    }
  }, FRAME_MS);
}
function togglePause() {
  const ov = document.getElementById("overlay");
  slideshowState.paused = !slideshowState.paused;
  if (slideshowState.paused) ov.classList.add("paused"); else ov.classList.remove("paused");
}
function closeOverlay() {
  const ov = document.getElementById("overlay");
  ov.classList.remove("open"); ov.classList.remove("paused");
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  slideshowState.imgs.forEach(im => { im.classList.remove("active"); im.removeAttribute("src"); im.style.display = "none"; });
  slideshowState.imgs = []; slideshowState.cur = 0; slideshowState.paused = false; slideshowState.activeCount = 0; slideshowState.srcs = [];
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeOverlay(); return; }
  if (e.key === " " || e.code === "Space") {
    const ov = document.getElementById("overlay");
    if (ov.classList.contains("open")) { e.preventDefault(); togglePause(); }
  }
});
document.getElementById("overlay").addEventListener("click", e => {
  const ov = document.getElementById("overlay");
  if (!ov.classList.contains("open")) return;
  if (e.target.classList.contains("close")) return;
  if (e.target.tagName === "IMG") togglePause();
  else if (e.target.id === "overlay" || e.target.classList.contains("frame") || e.target.classList.contains("caption")) {
    if (e.target.id === "overlay") closeOverlay(); else togglePause();
  }
});
document.getElementById("overlay").addEventListener("contextmenu", e => true);

document.querySelectorAll("th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = true; }
    document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
    th.querySelector(".arrow").textContent = sortAsc ? "▲" : "▼";
    render();
  });
});
document.getElementById("mass_min") && (document.getElementById("mass_min").textContent = MASS_DATE_MIN);
document.getElementById("btn-expand-all").addEventListener("click", () => {
  const btn = document.getElementById("btn-expand-all");
  const allOrgs = new Set(DATA.map(r => r.org));
  const allExpanded = [...allOrgs].every(o => expandedOrgs.has(o));
  if (allExpanded) { expandedOrgs.clear(); btn.textContent = "Развернуть все"; }
  else { allOrgs.forEach(o => expandedOrgs.add(o)); btn.textContent = "Свернуть все"; }
  render();
});
render();
</script>
</body>
</html>
"""

# ============================== ОЧИСТКА IMG ==============================
def _collect_used_screens(records):
    used = set()
    for r in records:
        for k in ("scr_a", "scr_b", "scr_c", "scr_pdf"):
            v = r.get(k) or ""
            if v: used.add(v)
        for s in r.get("screens") or []:
            if s: used.add(s)
    if DOWNLOAD_ERRORS_SRC.exists():
        try:
            err_text = DOWNLOAD_ERRORS_SRC.read_text(encoding="utf-8", errors="replace")
            for m in RE_SCR_IN_ERRLOG.finditer(err_text): used.add(m.group(1))
        except Exception:
            pass
    return used

def trash_unused_imgs(records):
    used = _collect_used_screens(records)
    all_imgs = sorted(IMG_DIR.glob("*.png"), key=lambda p: p.name)
    unused = [p for p in all_imgs if p.name not in used]
    if not unused: return
    try: TRASH_DIR.mkdir(parents=True, exist_ok=True)
    except Exception: return
    for p in unused:
        try:
            target = TRASH_DIR / p.name
            if not target.exists(): shutil.move(str(p), str(target))
        except Exception:
            pass

# ============================== MAIN ==============================
def main():
    if not LOG_DIR.exists() or not IMG_DIR.exists():
        return

    print("=== make_report2 — интерактивный опрос ===")
    upd_ogrn = ask_yes_no("Обновить поиск ОГРН и ИНН в чанках скринов договоров?", "n", 10)
    upd_pdf = ask_yes_no("Обновить закачку только новых выписок с egrul.nalog.ru?", "n", 10)
    upd_xml = ask_yes_no("Обновить две БД (полных и коротких имён) потерянных ЮЛ?", "n", 10)
    upd_grph = ask_yes_no("Обновить графы аффилированности?", "n", 10)

    if upd_ogrn: run_script("find_4scr_ogrn_inn.py")
    if upd_pdf:  run_script("egrul_deep_pdf_lost.py")
    if upd_xml:  run_script("egrul_parse_deep_pdf_lost.py")
    if upd_grph: run_script("build_graphs.py")

    print("\n=== Обновления завершены, генерируем отчёт ===\n")

    egrul_directors = load_egrul_directors(EGRUL_XML)
    print(f"[+] ЕГРЮЛ: {EGRUL_XML} -> {len(egrul_directors)} записей")

    egrul_full_all = load_egrul_short_data(EGRUL_SHORT_XML)
    egrul_lost = load_egrul_short_data(EGRUL_LOST_SHORT_XML)
    egrul_full_deep = load_egrul_short_data(EGRUL_XML)
    print(f"[+] ЕГРЮЛ (полный, fallback): {EGRUL_XML} -> {len(egrul_full_deep)} записей")

    ogrns_lost_map = load_ogrns_lost(OGRNS_LOST_TXT)
    fio_lost_map = load_fio_lost(FIO_LOST_TXT)

    graphs_data = {"by_fio": {}, "by_org": {}}
    if GRAPHS_JSON.exists():
        try:
            with open(GRAPHS_JSON, "r", encoding="utf-8") as fh:
                graphs_data = json.load(fh)
            print(f"[+] graphs.json загружен")
        except Exception as e:
            print(f"[!] Ошибка чтения graphs.json: {e}")
    else:
        print("[!] graphs.json не найден. Тултипы графов будут пустыми.")

    avar_set = load_avar(AVAR_TXT)
    print(f"[+] avar.txt -> {len(avar_set)} аварийных адресов")
    print(f"[+] ogrns_lost.txt -> {len(ogrns_lost_map)} записей")
    print(f"[+] fio_lost.txt -> {len(fio_lost_map)} записей")

    records = parse_contracts_logs()
    if not records: return

    before = len(records)
    records = [r for r in records if r["addr"].strip() not in avar_set]
    print(f"[+] записей: {before}, после фильтра аварийных: {len(records)}")
    if not records: return

    used_ogrns = set()
    for r in records:
        o = r.get("ogrn") or ""
        if o: used_ogrns.add(o)

    egrul_for_html = {o: egrul_full_all[o] for o in used_ogrns if o in egrul_full_all}

    for r in records:
        if r.get("scr_a"):
            png_path = IMG_DIR / r["scr_a"]
            chunks = read_png_chunks(png_path)
            desc = chunks.get("Description", "")
            total, living, non_living = extract_areas_from_desc(desc)
            r["total_area"] = total
            r["living_area"] = living
            r["non_living_area"] = non_living
            r["odi_delta"] = total - living - non_living

            url = chunks.get("Source", "")
            if url: r["house_url"] = url.strip()

    org_stats = parse_gisjkh_logs()
    mass_per_org = compute_mass_date_per_org(records)
    org_summary = compute_org_summary(records, org_stats, mass_per_org,
                                      egrul_directors, egrul_full_all)

    fio_to_ogrn = defaultdict(list)
    for ogrn, e in {**egrul_full_all, **egrul_lost}.items():
        d_raw = (e.get("director") or "").strip()
        if not d_raw:
            full = egrul_full_deep.get(ogrn) or {}
            d_raw = (full.get("director") or "").strip()
        if not d_raw: continue
        d = norm_fio(d_raw)
        if d and ogrn not in fio_to_ogrn[d]:
            fio_to_ogrn[d].append(ogrn)

    for r in records:
        scr_pdf = r.get("scr_pdf") or ""

        if scr_pdf and scr_pdf in ogrns_lost_map:
            r["found_ogrn"] = ogrns_lost_map[scr_pdf]
        else:
            r["found_ogrn"] = ""

        r["found_fio"] = ""
        r["found_fio_ogrn"] = ""
        r["found_fio_inn"] = ""
        if (not r["found_ogrn"]) and scr_pdf and scr_pdf in fio_lost_map:
            fio = fio_lost_map[scr_pdf]
            candidates = fio_to_ogrn.get(fio, [])
            cur_ogrn = ""
            if r["org"] in org_summary and org_summary[r["org"]]["ogrns"]:
                cur_ogrn = org_summary[r["org"]]["ogrns"][0]
            for cand in candidates:
                if cand != cur_ogrn:
                    r["found_fio"] = fio
                    r["found_fio_ogrn"] = cand
                    e = egrul_lost.get(cand) or egrul_full_all.get(cand) or {}
                    inn_cand = (e.get("director_inn") or "")
                    if not inn_cand:
                        full = egrul_full_deep.get(cand) or {}
                        inn_cand = (full.get("director_inn") or "")
                    r["found_fio_inn"] = inn_cand
                    break

    for r in records:
        cur_ogrn = ""
        if r["org"] in org_summary and org_summary[r["org"]]["ogrns"]:
            cur_ogrn = org_summary[r["org"]]["ogrns"][0]
        cur_inn = ""
        if cur_ogrn and cur_ogrn in egrul_for_html:
            cur_inn = egrul_for_html[cur_ogrn].get("ul_inn") or ""

        r["is_mismatch"] = False
        fo = r.get("found_ogrn") or ""
        if fo:
            if len(fo) == 10:
                r["is_mismatch"] = (fo != cur_inn) if cur_inn else False
            else:
                r["is_mismatch"] = (fo != cur_ogrn)

        r["is_mismatch_fio"] = False
        if (not r["is_mismatch"]) and r.get("found_fio"):
            cur_dir_raw = ""
            if cur_ogrn and cur_ogrn in egrul_for_html:
                cur_dir_raw = (egrul_for_html[cur_ogrn].get("director") or "")
            if not cur_dir_raw and cur_ogrn:
                full = egrul_full_deep.get(cur_ogrn) or {}
                cur_dir_raw = (full.get("director") or "")
            cur_dir = norm_fio(cur_dir_raw)
            r["is_mismatch_fio"] = (r["found_fio"].upper() != cur_dir.upper())

    if DOWNLOAD_ERRORS_SRC.exists():
        try:
            shutil.copyfile(DOWNLOAD_ERRORS_SRC, DOWNLOAD_ERRORS_DST)
            print(f"[+] download_errors.log → {DOWNLOAD_ERRORS_DST}")
        except Exception as e:
            print(f"[!] ошибка: {e}")

    data_js = json.dumps(records, ensure_ascii=False)
    org_js = json.dumps(org_stats, ensure_ascii=False)
    summary_js = json.dumps(org_summary, ensure_ascii=False)
    egrul_js = json.dumps(egrul_for_html, ensure_ascii=False)
    egrul_lost_js = json.dumps(egrul_lost, ensure_ascii=False)
    egrul_full_js = json.dumps(egrul_full_deep, ensure_ascii=False)
    graphs_js = json.dumps(graphs_data, ensure_ascii=False)
    mass_min_js = str(MASS_DATE_MIN)
    mass_hint_js = json.dumps(MASS_DATE_HINT, ensure_ascii=False)

    html_text = (HTML_TEMPLATE
        .replace("__DATA__", data_js)
        .replace("__ORG_STATS__", org_js)
        .replace("__ORG_SUMMARY__", summary_js)
        .replace("__EGRUL_ENTITIES__", egrul_js)
        .replace("__EGRUL_LOST__", egrul_lost_js)
        .replace("__EGRUL_FULL__", egrul_full_js)
        .replace("__GRAPHS__", graphs_js)
        .replace("__MASS_DATE_MIN__", mass_min_js)
        .replace("__MASS_DATE_HINT__", mass_hint_js))

    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(f"[+] report: {OUT_HTML}")

if __name__ == "__main__":
    main()
