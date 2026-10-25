# -*- coding: utf-8 -*-
"""
make_report2.py — генератор HTML-отчёта по проверке ЭП договоров УК.
Итерация 3.

CLI:
    python make_report2.py
    python make_report2.py --no-trash
    python make_report2.py --only-ogrn 1065506006257
    python make_report2.py --no-egrul
"""

from __future__ import annotations
import os
import re
import sys
import json
import shutil
import argparse
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict
from datetime import date as _date
from tempfile import gettempdir


# ============================================================
# ПУТИ
# ============================================================
BASE_DIR   = Path(gettempdir()) / "IN"
LOG_DIR    = BASE_DIR / "LOG"
IMG_DIR    = BASE_DIR / "IMG"
TRASH_DIR  = BASE_DIR / "OLD" / "IMG"
OUT_HTML   = IMG_DIR / "report.html"

XML_212    = Path(r"D:\DB\XML\egrul_omsk_212.xml")
XML_DEEP   = Path(r"D:\DB\XML\egrul_omsk_deep_final.xml")

SCRIPT_DIR = Path(__file__).resolve().parent
OGRNS_ACT  = SCRIPT_DIR / "ogrns_act.txt"
NPA_LIST   = SCRIPT_DIR / "npa_list.txt"
DL_ERRORS  = SCRIPT_DIR / "download_errors.log"


# ============================================================
# КОНСТАНТЫ
# ============================================================
MARKER_LONG = {
    "Н": "Подпись верна, но НЕДЕЙСТВИТЕЛЬНА",
    "Ж": "Подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА",
    "Э": "Подпись ОТСУТСТВУЕТ",
    "О": "Информация о договоре НЕ РАЗМЕЩЕНА",
    "Д": "Подпись ДЕЙСТВИТЕЛЬНА",
}

MASS_DATE_MIN      = 2
MASS_JOKE_DATES    = {"01.01", "01.04", "01.05", "08.03", "23.02", "09.05", "12.06", "04.11"}
LOW_CAPITAL_VALUE  = 10000.0

TECH_FAIL_MARKERS = {
    "ZIP_FAIL", "NO_DOWNLOAD_BTN", "PDF_HTML_STUB", "SIGN_INVALID_FORMAT",
    "UIDS_PARAM_MISSING", "O_NO_LINK", "O_NO_FILES", "E_NO_SIGN", "E_UNKNOWN",
}
TECH_OK_MARKERS = {"ZIP_FAIL_FALLBACK_OK"}


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================
def norm_name(s: str) -> str:
    if not s:
        return ""
    s = s.upper().replace("Ё", "Е")
    s = s.replace("«", " ").replace("»", " ").replace('"', " ").replace("'", " ")
    s = re.sub(r"\s+", " ", s).strip()
    repl = [
        (r"\bОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ\b", "ООО"),
        (r"\bПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО\b", "ПАО"),
        (r"\bЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО\b", "ЗАО"),
        (r"\bАКЦИОНЕРНОЕ ОБЩЕСТВО\b", "АО"),
        (r"\bУПРАВЛЯЮЩАЯ КОМПАНИЯ\b", "УК"),
        (r"\bУПРАВЛЯЮЩАЯ ОРГАНИЗАЦИЯ\b", "УО"),
        (r"\bЖИЛИЩНО-КОММУНАЛЬНАЯ ОРГАНИЗАЦИЯ\b", "ЖКО"),
        (r"\bЖИЛИЩНАЯ КОММУНАЛЬНАЯ ОРГАНИЗАЦИЯ\b", "ЖКО"),
        (r"\bЖИЛИЩНО-КОММУНАЛЬНОЕ ХОЗЯЙСТВО\b", "ЖКХ"),
        (r"\bЖИЛИЩНО-КОММУНАЛЬНОЕ ХОЗЯЙСТВО\b", "ЖКХ"),
        (r"\bДИРЕКЦИЯ ЭКСПЛУАТАЦИИ ЗДАНИЙ\b", "ДЭЗ"),
        (r"\bТОВАРИЩЕСТВО СОБСТВЕННИКОВ ЖИЛЬЯ\b", "ТСЖ"),
        (r"\bТОВАРИЩЕСТВО СОБСТВЕННИКОВ НЕДВИЖИМОСТИ\b", "ТСН"),
        (r"\bЖИЛИЩНО-СТРОИТЕЛЬНЫЙ КООПЕРАТИВ\b", "ЖСК"),
    ]
    for pat, rep in repl:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def norm_name_short(s: str) -> str:
    n = norm_name(s)
    n = re.sub(r"^(ООО|АО|ПАО|ЗАО|ТСЖ|ЖСК|ТСН)\s+", "", n)
    return n.strip()


def norm_address(s: str) -> str:
    if not s:
        return ""
    s = s.upper().replace("Ё", "Е")
    s = re.sub(r"Г\.О\.\s*ГОРОД ОМСК,?\s*Г\.\s*ОМСК,?\s*", "", s)
    s = re.sub(r"ОМСКАЯ ОБЛАСТЬ,?\s*", "", s)
    s = re.sub(r"СТРАНИЦА\s+\d+\s+ИЗ.*$", "", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(",")
    return s


def address_kind(norm: str) -> str:
    if re.search(r"(^|[\s,])(КВ\.|КВАРТИРА|КОМ\.)", norm):
        return "home"
    return "office"


def norm_fio(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.upper().replace("Ё", "Е")).strip()


def compute_form(name: str) -> str:
    if not name:
        return "?"
    n = name.upper()
    if "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО" in n:
        return "ПАО"
    if "ЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО" in n:
        return "ЗАО"
    if "АКЦИОНЕРНОЕ ОБЩЕСТВО" in n:
        return "АО"
    if "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ" in n:
        return "ООО"
    if "ТОВАРИЩЕСТВО СОБСТВЕННИКОВ ЖИЛЬЯ" in n:
        return "ТСЖ"
    if "ЖИЛИЩНО-СТРОИТЕЛЬНЫЙ КООПЕРАТИВ" in n:
        return "ЖСК"
    if "ТОВАРИЩЕСТВО СОБСТВЕННИКОВ НЕДВИЖИМОСТИ" in n:
        return "ТСН"
    return "?"


def parse_date_ddmmyyyy(s: str):
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", (s or "").strip())
    if not m:
        return None
    try:
        return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


# ============================================================
# ПРАЗДНИКИ
# ============================================================
try:
    import holidays as _holidays
    RU_HOLIDAYS = _holidays.RU(years=range(2000, 2032))
    _HAS_HOLIDAYS = True
except Exception:
    RU_HOLIDAYS = None
    _HAS_HOLIDAYS = False


def is_holiday(date_str: str) -> bool:
    d = parse_date_ddmmyyyy(date_str)
    if not d:
        return False
    if _HAS_HOLIDAYS and RU_HOLIDAYS is not None:
        try:
            if d in RU_HOLIDAYS:
                return True
        except Exception:
            pass
    return d.weekday() >= 5


# ============================================================
# ЕГРЮЛ
# ============================================================
RE_BROKEN_DIRECTOR = re.compile(r"<director/>([^<]*)</director>")

def _read_xml_sanitized(path: Path) -> ET.Element:
    raw = path.read_text(encoding="utf-8")
    raw = RE_BROKEN_DIRECTOR.sub(r"<director>\1</director>", raw)
    return ET.fromstring(raw)


RE_OGRN = re.compile(r"\b(\d{13}|\d{15})\b")

def load_act_ogrns(path: Path = OGRNS_ACT) -> list[str]:
    ogrns = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = RE_OGRN.search(line)
        if m:
            ogrns.append(m.group(1))
    return ogrns


def parse_212(path: Path) -> dict[str, dict]:
    root = _read_xml_sanitized(path)
    out = {}
    for ent in root.iter("entity"):
        ogrn = (ent.findtext("ogrn") or "").strip()
        if not ogrn:
            continue
        out[ogrn] = {
            "ogrn": ogrn,
            "inn": (ent.findtext("inn") or "").strip(),
            "kpp": (ent.findtext("kpp") or "").strip(),
            "name_full": (ent.findtext("name_full") or "").strip(),
            "name_short": (ent.findtext("name_short") or "").strip(),
            "status": (ent.findtext("status") or "").strip(),
            "status_date": (ent.findtext("status_date") or "").strip(),
            "termination_date": (ent.findtext("termination_date") or "").strip(),
        }
    return out


def parse_deep(path: Path) -> dict[str, dict]:
    root = _read_xml_sanitized(path)
    out = {}
    for ent in root.iter("entity"):
        ogrn = (ent.findtext("ogrn") or "").strip()
        if not ogrn:
            continue
        founders = []
        f_el = ent.find("founders")
        if f_el is not None:
            for fo in f_el.findall("founder"):
                name = (fo.findtext("name") or "").strip()
                inn = (fo.findtext("inn") or "").strip()
                if name or inn:
                    founders.append({"name": name, "inn": inn})
        cap_raw = (ent.findtext("capital") or "").strip()
        try:
            capital = float(cap_raw.replace(",", ".")) if cap_raw else None
        except ValueError:
            capital = None
        out[ogrn] = {
            "ogrn": ogrn,
            "name": (ent.findtext("name") or "").strip(),
            "director": (ent.findtext("director") or "").strip(),
            "director_inn": (ent.findtext("director_inn") or "").strip(),
            "address": (ent.findtext("address") or "").strip(),
            "capital": capital,
            "founders": founders,
        }
    return out


class Egrul:
    def __init__(self, entities: dict[str, dict]):
        self.entities = entities
        self.by_inn: dict[str, list[str]] = defaultdict(list)
        self.by_name_norm: dict[str, list[str]] = defaultdict(list)
        self.by_name_norm_short: dict[str, list[str]] = defaultdict(list)
        self.directors_by_inn: dict[str, list[str]] = defaultdict(list)
        self.founders_by_inn: dict[str, list[str]] = defaultdict(list)
        self.by_address_norm: dict[str, dict] = {}
        self.persons_by_org: dict[str, dict[str, dict]] = {}
        self._build_indexes()

    def _build_indexes(self):
        for ogrn, e in self.entities.items():
            if e.get("inn"):
                self.by_inn[e["inn"]].append(ogrn)
            nn = norm_name(e.get("name_short") or e.get("name") or e.get("name_full") or "")
            if nn:
                self.by_name_norm[nn].append(ogrn)
            nns = norm_name_short(e.get("name_short") or e.get("name") or e.get("name_full") or "")
            if nns:
                self.by_name_norm_short[nns].append(ogrn)

            addr_norm = norm_address(e.get("address") or "")
            if addr_norm:
                if addr_norm not in self.by_address_norm:
                    self.by_address_norm[addr_norm] = {
                        "display": e["address"],
                        "kind": address_kind(addr_norm),
                        "ogrns": [],
                    }
                self.by_address_norm[addr_norm]["ogrns"].append(ogrn)

            persons: dict[str, dict] = {}
            d_fio = norm_fio(e.get("director") or "")
            d_inn = (e.get("director_inn") or "").strip()
            if d_inn and d_fio:
                persons[d_inn] = {"fio": d_fio, "role": "директор"}
                self.directors_by_inn[d_inn].append(ogrn)
            for f in e.get("founders") or []:
                f_inn = (f.get("inn") or "").strip()
                f_fio = norm_fio(f.get("name") or "")
                if not f_inn or not f_fio:
                    continue
                if f_inn in persons:
                    if persons[f_inn]["role"] == "директор":
                        persons[f_inn]["role"] = "директор, учредитель"
                else:
                    persons[f_inn] = {"fio": f_fio, "role": "учредитель"}
                self.founders_by_inn[f_inn].append(ogrn)
            self.persons_by_org[ogrn] = persons

    def display_name(self, ogrn: str) -> str:
        e = self.entities.get(ogrn)
        if not e:
            return ogrn
        return e.get("name_short") or e.get("name") or e.get("name_full") or ogrn

    def display_form(self, ogrn: str) -> str:
        e = self.entities.get(ogrn)
        if not e:
            return "?"
        return compute_form(e.get("name_short") or e.get("name") or e.get("name_full") or "")

    def find_by_org_name(self, org: str) -> str | None:
        if not org:
            return None
        n = norm_name(org)
        if n in self.by_name_norm and len(self.by_name_norm[n]) == 1:
            return self.by_name_norm[n][0]
        nns = norm_name_short(org)
        if nns in self.by_name_norm_short and len(self.by_name_norm_short[nns]) == 1:
            return self.by_name_norm_short[nns][0]
        return None

    def neighbors_by_person(self, ogrn: str) -> dict[str, dict]:
        result: dict[str, dict] = {}
        persons = self.persons_by_org.get(ogrn, {})
        for inn, p in persons.items():
            for nb in self.directors_by_inn.get(inn, []):
                if nb == ogrn:
                    continue
                self._add_neighbor(result, nb, p["fio"], inn, "директор")
            for nb in self.founders_by_inn.get(inn, []):
                if nb == ogrn:
                    continue
                self._add_neighbor(result, nb, p["fio"], inn, "учредитель")
        return result

    def _add_neighbor(self, result, nb_ogrn, fio, inn, role):
        if nb_ogrn not in result:
            result[nb_ogrn] = {"org": self.entities[nb_ogrn], "persons": []}
        item = result[nb_ogrn]
        if not any(pp["inn"] == inn and pp["role"] == role for pp in item["persons"]):
            item["persons"].append({"fio": fio, "inn": inn, "role": role})

    def address_neighbors(self, ogrn: str) -> dict | None:
        e = self.entities.get(ogrn)
        if not e:
            return None
        addr_norm = norm_address(e.get("address") or "")
        if not addr_norm:
            return None
        bucket = self.by_address_norm.get(addr_norm)
        if not bucket:
            return None
        others = [o for o in bucket["ogrns"] if o != ogrn]
        if not others:
            return None
        return {
            "display": bucket["display"],
            "kind": bucket["kind"],
            "ogrns": others,
        }


def load_egrul(filter_by_act: bool = True) -> Egrul:
    s212 = parse_212(XML_212)
    sdeep = parse_deep(XML_DEEP)
    merged: dict[str, dict] = {}
    for ogrn, e212 in s212.items():
        ed = sdeep.get(ogrn, {})
        merged[ogrn] = {**e212, **{k: v for k, v in ed.items() if k != "ogrn"}}
    for ogrn, ed in sdeep.items():
        if ogrn not in merged:
            merged[ogrn] = ed
    if filter_by_act:
        act = set(load_act_ogrns())
        merged = {o: e for o, e in merged.items() if o in act}
    return Egrul(merged)


# ============================================================
# ПАРСЕР contracts*.log
# ============================================================
RE_SCR = r"scr(?:\d{6}|XXXXXX)\.png"
RE_LINE1 = re.compile(
    r"^(" + RE_SCR + r")\s*-\s*(.+?)\s*--\s*(.+?)\s*---\s*(.+?)\s*----\s*(.+)$"
)
RE_LINE2 = re.compile(
    r"^(" + RE_SCR + r")\s*-\s*(\([^)]*\)-[^=]*=-?[\d.,]+)\s*--\s*(.*)$"
)
RE_LINE3 = re.compile(r"^(" + RE_SCR + r")\s*-\s*(.+)$")
RE_LINE4_SCR = re.compile(r"^(" + RE_SCR + r")\s*$")

RE_DELTA = re.compile(r"\(([\d.,]+)\)-([\d.,]+)=(-?[\d.,]+)")
RE_FILENAME_OGRN = re.compile(r"^contracts(\d{10,})\.log$")


def _iter_contract_files():
    return sorted(LOG_DIR.glob("contracts*.log"), key=lambda p: p.name)


def _extract_ogrn(filename: str) -> str:
    m = RE_FILENAME_OGRN.match(filename)
    return m.group(1) if m else ""


def _parse_delta(s: str) -> float | None:
    m = RE_DELTA.search(s or "")
    if not m:
        return None
    try:
        return round(float(m.group(3).replace(",", ".")), 2)
    except ValueError:
        return None


def _is_real_scr(name: str) -> bool:
    return bool(name) and bool(re.match(r"^scr\d{6}\.png$", name))


def parse_contracts_logs(egrul: Egrul | None) -> list[dict]:
    records: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    for path in _iter_contract_files():
        ogrn = _extract_ogrn(path.name)
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = re.split(r"\r?\n\r?\n", text.strip())
        for block in blocks:
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
            if len(lines) < 3:
                continue

            m1 = RE_LINE1.match(lines[0].strip())
            if not m1:
                continue
            scr_a, org, addr, pdf_label, date_str = m1.groups()
            org = org.strip()
            if not org:
                continue

            m2 = RE_LINE2.match(lines[1].strip())
            if not m2:
                continue
            scr_b, delta_str, synopsis = m2.groups()

            m3 = RE_LINE3.match(lines[2].strip())
            if not m3:
                continue
            scr_c, marker = m3.groups()
            marker = marker.strip()

            screens = [scr_a, scr_b, scr_c]
            for ln in lines[3:]:
                m4 = RE_LINE4_SCR.match(ln.strip())
                if m4:
                    screens.append(m4.group(1))

            delta_value = _parse_delta(delta_str)

            org_norm = norm_name(org)
            addr_norm = norm_address(addr)
            key = (org_norm, addr_norm)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            org_ogrn = egrul.find_by_org_name(org) if egrul else None

            records.append({
                "src": path.name,
                "ogrn": ogrn,
                "org": org,
                "org_norm": org_norm,
                "org_ogrn_egrul": org_ogrn,
                "addr": addr,
                "addr_norm": addr_norm,
                "pdf_label": pdf_label,
                "date": date_str,
                "scr_a": scr_a,
                "scr_b": scr_b,
                "scr_c": scr_c,
                "screens": screens,
                "has_real_screens": any(_is_real_scr(s) for s in screens),
                "marker": marker,
                "marker_long": MARKER_LONG.get(marker, marker),
                "delta_value": delta_value,
                "delta_str": delta_str,
                "synopsis": synopsis.strip(),
                "is_holiday": is_holiday(date_str),
            })
    return records


# ============================================================
# ПАРСЕР gisjkh-ufo*.log
# ============================================================
RE_HOUSE_INFO = re.compile(
    r"house_info:\s+"
    r"org='([^']*)'\s+"
    r"addr='([^']*)'\s+"
    r"pdf='([^']*)'\s+"
    r"real='([^']*)'\s+"
    r"date='([^']*)'\s+"
    r"pdf_icons=(\d+)"
)
RE_RESULT_MARKER = re.compile(
    r"result_text:\s*'([^']*)'\s+marker:\s*'([^']*)'"
)
RE_RESULT_NONE = re.compile(
    r"result_text:\s*'ДЕЙСТВИТЕЛЬНА'\s+marker:\s*None"
)
RE_RESULT_LINE = re.compile(r"\[result\]\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$")
RE_SQUARES = re.compile(r"\[squares-(card|final)\]\s+total='([^']*)'\s+res='([^']*)'")
RE_COLLAGE_PAGES = re.compile(r"\[collage\]\s+страниц в PDF:\s*(\d+),\s*берём idx=\[([^\]]*)\]")
RE_COLLAGE_OCR = re.compile(r"\[collage\]\s+стр\.(\d+):\s+OCR\s+(\d+)\s+симв\.")
RE_CONTRACT_LOG_EO = re.compile(
    r"\[contract-log-EO\]\s+written:\s*(" + RE_SCR + r")\s*-\s*(\w+)\s*\|\s*delta=(\S+)"
)


def _iter_gisjkh_files():
    return sorted(LOG_DIR.glob("gisjkh-ufo*.log"), key=lambda p: p.name)


def parse_gisjkh_logs() -> dict[str, dict]:
    stats: dict[str, dict] = {}

    for path in _iter_gisjkh_files():
        m = re.match(r"^gisjkh-ufo(\d{10,})\.log$", path.name)
        if not m:
            continue
        ogrn = m.group(1)
        s = stats.setdefault(ogrn, {
            "ogrn": ogrn,
            "houses": 0,
            "markers": defaultdict(int),
            "tech": defaultdict(int),
            "fallback_ok": 0,
            "squares": {},
        })

        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.rstrip()

            m_hi = RE_HOUSE_INFO.search(line)
            if m_hi:
                s["houses"] += 1
                continue

            m_res = RE_RESULT_MARKER.search(line)
            if m_res:
                _txt, marker = m_res.group(1).strip(), m_res.group(2).strip()
                if marker in ("Н", "Ж", "Э", "О"):
                    s["markers"][marker] += 1
                continue

            if RE_RESULT_NONE.search(line):
                s["markers"]["Д"] += 1
                continue

            m_res_line = RE_RESULT_LINE.search(line)
            if m_res_line:
                a, b, c = (m_res_line.group(1).strip(),
                           m_res_line.group(2).strip(),
                           m_res_line.group(3).strip())
                for seg in (a, b, c):
                    seg_up = seg.upper()
                    for tk in TECH_FAIL_MARKERS:
                        if tk in seg_up:
                            s["tech"][tk] += 1
                            break
                    for tk in TECH_OK_MARKERS:
                        if tk in seg_up:
                            s["fallback_ok"] += 1
                            break
                continue

            m_eo = RE_CONTRACT_LOG_EO.search(line)
            if m_eo:
                continue

            m_sq = RE_SQUARES.search(line)
            if m_sq:
                kind, total, res = m_sq.groups()
                if kind == "final":
                    s["squares"] = {"total": total, "res": res}
                continue

    return stats


# ============================================================
# АНАЛИТИКА
# ============================================================
def compute_mass_dates(records: list[dict]) -> dict[str, dict]:
    counts_per_org: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    order_per_org: dict[str, list[str]] = defaultdict(list)
    for r in records:
        org = r["org_norm"]
        date = (r["date"] or "").strip()
        if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", date):
            continue
        if date not in counts_per_org[org]:
            order_per_org[org].append(date)
        counts_per_org[org][date] += 1

    result: dict[str, dict] = {}
    for org, counts in counts_per_org.items():
        mass = [(d, c) for d, c in counts.items() if c >= MASS_DATE_MIN]
        if not mass:
            continue
        max_count = max(c for _, c in mass)
        for d in order_per_org[org]:
            if counts.get(d) == max_count and counts[d] >= MASS_DATE_MIN:
                is_joke = d[:5] in MASS_JOKE_DATES
                result[org] = {"date": d, "count": max_count, "is_joke": is_joke}
                break
    return result


def compute_org_summary(
    records: list[dict],
    egrul: Egrul | None,
    gisjkh_stats: dict[str, dict],
    mass_dates: dict[str, dict],
) -> dict[str, dict]:
    summary: dict[str, dict] = {}

    for r in records:
        org = r["org_norm"]
        if org not in summary:
            summary[org] = {
                "org": r["org"],
                "org_norm": org,
                "org_ogrn_egrul": r.get("org_ogrn_egrul"),
                "addr_count": 0,
                "n_count": 0, "j_count": 0, "e_count": 0, "o_count": 0, "d_count": 0,
                "mass_date": "", "mass_count": 0, "mass_joke": False,
                "trust": 0.0,
                "neg_count": 0, "neg_sum": 0.0,
                "holiday_count": 0,
                "low_capital": False,
                "ghost_uk": False,
                "tech_fail_count": 0,
                "ogrns": set(),
            }
        s = summary[org]
        s["addr_count"] += 1
        marker = r["marker"]
        if marker == "Н": s["n_count"] += 1
        elif marker == "Ж": s["j_count"] += 1
        elif marker == "Э": s["e_count"] += 1
        elif marker == "О": s["o_count"] += 1
        if r.get("org_ogrn_egrul"):
            s["ogrns"].add(r["org_ogrn_egrul"])
        dv = r.get("delta_value")
        if dv is not None and dv < 0:
            s["neg_count"] += 1
            s["neg_sum"] += dv
        if r.get("is_holiday"):
            s["holiday_count"] += 1

    if egrul:
        for ogrn, gs in gisjkh_stats.items():
            name = egrul.display_name(ogrn)
            key = norm_name(name)
            if key in summary:
                summary[key]["d_count"] = gs["markers"].get("Д", 0)

    for org, s in summary.items():
        d = s["d_count"]
        n, j, e, o = s["n_count"], s["j_count"], s["e_count"], s["o_count"]
        denom = d + n + j + e + o
        s["trust"] = round(d / denom * 100, 1) if denom else 0.0
        s["neg_sum"] = round(s["neg_sum"], 2)
        if org in mass_dates:
            m = mass_dates[org]
            s["mass_date"] = m["date"]
            s["mass_count"] = m["count"]
            s["mass_joke"] = m["is_joke"]

        if egrul and s["ogrns"]:
            ogrn = sorted(s["ogrns"])[0]
            ent = egrul.entities.get(ogrn)
            if ent:
                if (ent.get("status") or "") == "terminated":
                    s["ghost_uk"] = True
                if ent.get("capital") is not None and abs(ent["capital"] - LOW_CAPITAL_VALUE) < 0.5:
                    s["low_capital"] = True

        if egrul and s["ogrns"]:
            ogrn = sorted(s["ogrns"])[0]
            gs = gisjkh_stats.get(ogrn)
            if gs:
                s["tech_fail_count"] = sum(gs["tech"].values())

    return summary


# ============================================================
# NPA_LIST
# ============================================================
NPA_TEXT = """Перечень нормативных правовых актов, использованных при подготовке отчёта

1. Конституция Российской Федерации.
2. Жилищный кодекс Российской Федерации.
3. Гражданский кодекс Российской Федерации (ст. 160, 432, 388, 391, 1102).
4. Федеральный закон от 21.07.2014 № 209-ФЗ «О государственной информационной системе жилищно-коммунального хозяйства» (п. 8 ст. 4, п. 4 ст. 5).
5. Федеральный закон от 27.07.2006 № 149-ФЗ «Об информации, информационных технологиях и о защите информации» (ч. 9 ст. 14).
6. Федеральный закон от 06.04.2011 № 63-ФЗ «Об электронной подписи».
7. Кодекс Российской Федерации об административных правонарушениях (ст. 20.4).
8. Уголовный кодекс Российской Федерации (ст. 159, 159.2, 272, 327).
9. Постановление Правительства РФ от 13.08.2006 № 491 «Об утверждении Правил содержания общего имущества в многоквартирном доме…».
10. Постановление Правительства РФ от 06.05.2011 № 354 «О предоставлении коммунальных услуг собственникам и пользователям помещений…».
"""


# ============================================================
# DOWNLOAD_ERRORS
# ============================================================
DL_HEADER = (
    "Лог сбоев при скачивании с портала ГИС ЖКХ.\n"
    "Лог относителен. При размере файла > 40 МБ допускаются таймауты и повторные попытки.\n"
    "Формат: <таймстамп> - <ОГРН> -- <ЮЛ> --- <адрес> ---- <файл> ----- <url> ------ <тип> ------- download error\n"
    "----------------------------------------------------------------------------------------------------\n"
)


# ============================================================
# HTML-ШАБЛОН
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Отчёт по проверке ЭП договоров УК</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         margin: 0; padding: 20px; background: #f5f5f5; color: #222;
         font-size: 13px; line-height: 1.5; }
  h1 { font-size: 20px; margin: 0 0 8px 0; }
  h2 { font-size: 15px; margin: 18px 0 8px 0; }
  .meta { color: #666; font-size: 12px; margin-bottom: 16px; }

  blockquote.epigraph {
    text-align: right;
    margin: 24px 40px 24px auto;
    max-width: 720px;
    font-size: 13px;
    line-height: 1.55;
    color: #333;
    border-left: none;
    padding: 0;
  }
  blockquote.epigraph p { margin: 0 0 10px 0; }
  blockquote.epigraph b { color: #000; font-size: 13.5px; }
  blockquote.epigraph i { font-style: italic; color: #555; }

  table.main-table { border-collapse: collapse; background: #fff; width: 100%;
          box-shadow: 0 1px 4px rgba(0,0,0,0.1); margin-top: 16px; }
  th, td { border: 1px solid #ddd; padding: 6px 8px; font-size: 13px;
           vertical-align: top; }
  th { background: #eaeaea; position: sticky; top: 0; cursor: pointer; user-select: none; }
  th.no-sort { cursor: default; }
  th .arrow { color: #888; font-size: 11px; margin-left: 4px; }

  tr.org-row td { background: #f3f3f3; font-weight: 600; }
  tr.org-row td.org-cell { white-space: normal; }
  td.org-cell .org-name { display: block; font-weight: 600; }
  td.org-cell .org-ogrn { display: block; font-weight: 400; color: #555;
                           font-size: 11px; font-variant-numeric: tabular-nums; }
  td.org-cell .org-score { display: block; font-weight: 700; text-align: right;
                            font-variant-numeric: tabular-nums; }

  td.toggle-cell { text-align: center; vertical-align: middle; cursor: pointer;
                    user-select: none; font-weight: bold; font-size: 16px; width: 28px; }
  td.toggle-cell:hover { background: #dcdcdc; }

  td.mass-date { font-weight: 600; }
  td.mass-date .mass-hint { display: block; font-weight: 400; font-size: 11px; color: #555; }

  td.holiday-date { color: #b00; font-weight: bold; }
  td.marker-zh { color: #b00; font-weight: bold; }
  td.marker-n  { color: #d68000; font-weight: bold; }
  td.marker-d  { color: #0a7a2a; font-weight: bold; }
  td.marker-other { color: #333; font-weight: bold; }

  .n-counter { color: #d68000; font-weight: bold; }
  .j-counter { color: #b00; font-weight: bold; margin-left: 10px; }
  .e-counter { color: #333; font-weight: bold; margin-left: 10px; }
  .o-counter { color: #333; font-weight: bold; margin-left: 10px; }

  td.addr-cell { display: flex; justify-content: space-between;
                  align-items: flex-start; gap: 12px; }
  td.addr-cell .addr-text { flex: 1 1 auto; }
  td.addr-cell .addr-delta { flex: 0 0 auto; color: #b00; font-weight: bold;
                             font-variant-numeric: tabular-nums; text-align: right; }

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
    display: none; z-index: 9999;
    align-items: center; justify-content: center;
  }
  #overlay.open { display: flex; }
  #overlay .frame {
    position: relative; max-width: 95vw; max-height: 95vh;
    width: 95vw; height: 95vh;
    display: flex; align-items: center; justify-content: center;
  }
  #overlay img {
    max-width: 100%; max-height: 100%;
    position: absolute; opacity: 0;
    transition: opacity 0.25s; object-fit: contain;
    user-select: none;
  }
  #overlay img.active { opacity: 1; }
  #overlay .close {
    position: absolute; top: 12px; right: 16px;
    color: #fff; font-size: 28px; cursor: pointer;
    background: rgba(0,0,0,0.4); border-radius: 50%;
    width: 40px; height: 40px; line-height: 36px; text-align: center;
    user-select: none;
  }
  #overlay .close:hover { background: rgba(255,255,255,0.2); }
  #overlay .caption {
    position: absolute; left: 16px; bottom: 12px;
    color: #ddd; font-size: 13px; font-family: monospace;
    background: rgba(0,0,0,0.5); padding: 6px 10px; border-radius: 4px;
  }
  #overlay .paused-badge {
    position: absolute; top: 12px; left: 16px;
    color: #fff; font-size: 13px; font-family: monospace;
    background: rgba(180, 0, 0, 0.8); padding: 4px 10px;
    border-radius: 4px; display: none;
  }
  #overlay.paused .paused-badge { display: block; }

  .tip {
    position: fixed;
    z-index: 10000;
    background: #ffffff;
    color: #222;
    border: 1px solid #c8c8c8;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 7pt;
    line-height: 1.4;
    max-width: 95vw;
    box-shadow: 0 6px 22px rgba(0,0,0,0.18);
    pointer-events: none;
    display: none;
    overflow: hidden;
  }
  .tip.open { display: block; }
  .tip .title {
    color: #0b5cad; font-weight: 700;
    margin-bottom: 3px;
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 7pt;
  }
  .tip .sect {
    color: #666; font-weight: 600;
    margin: 3px 0 1px 0;
    font-size: 7pt;
  }
  .tip .line { white-space: pre; color: #222; font-size: 7pt; }
  .tip .who { color: #444; white-space: pre; font-size: 7pt; }

  .legal { color: #444; font-size: 12px; margin-top: 32px; line-height: 1.55;
           border-top: 1px solid #ddd; padding-top: 12px; }
  .legal h2 { font-size: 14px; margin: 0 0 8px 0; }
  .legal ul { margin: 0 0 8px 0; padding-left: 20px; }
</style>
</head>
<body>

<h1>Отчёт по проверке квалифицированной ЭП договоров УК</h1>

<blockquote class="epigraph">
<p>
<b>Федеральный закон от 21.07.2014 № 209-ФЗ</b><br>
«О государственной информационной системе жилищно-коммунального хозяйства»<br>
<i>п. 8 ст. 4</i> — <i>«…принцип полноты, достоверности, актуальности информации и своевременности её размещения»</i>;<br>
<i>п. 4 ст. 5</i> — <i>«…достоверной и актуальной информации»</i>.
</p>
<p>
<b>Федеральный закон от 27.07.2006 № 149-ФЗ</b><br>
«Об информации, информационных технологиях и о защите информации»<br>
<i>ч. 9 ст. 14</i> — <i>«Информация, содержащаяся в государственных информационных системах, является официальной. Государственные органы… обязаны обеспечить достоверность и актуальность информации…»</i>.
</p>
</blockquote>

<div class="meta">
  Записей: <span id="count">0</span>.
  <br>
  <b>Расшифровка маркеров столбца «Результат проверки ЭП»:</b>
  <b>Н/Д</b> — подпись верна, но НЕДЕЙСТВИТЕЛЬНА;
  <b>Н/В</b> — подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА;
  <b>Н/Э</b> — подпись ОТСУТСТВУЕТ;
  <b>Н/О</b> — информация о договоре НЕ РАЗМЕЩЕНА.
  В отчёт не попадает только Д (подпись ДЕЙСТВИТЕЛЬНА) — Д идёт
  исключительно в статистику уровня доверия.
  <br>
  <b>Уровень доверия</b> по УК = Д / (Д + Н/Д + Н/В + Н/Э + Н/О) × 100%,
  где Д — дома с действительной ЭП, остальные — из расшифровки выше.
  Цвет — по тепловой шкале (голубой = высокий, бордовый = низкий).
  Сортировка по умолчанию — по уровню доверия <b>↑</b> (аутсайдеры сверху).
  <br>
  <b>Массовые одинаковые даты заключения договоров</b> (у одной УК, ≥2 раз)
  показаны в свёрнутой строке УК — берётся дата с максимальным
  числом повторов. Подсвечены пастельным цветом. Строки «Дата отсутствует»
  и прочие не-даты в подсчёт не входят.
  <br>
  <b>Отрицательная площадь</b> (красным справа от адреса) —
  разница между общей площадью дома и площадью жилых помещений,
  если она получилась меньше нуля. Показывается как
  <code>−N м<sup>2</sup></code> и только для таких домов.
  <br>
  <b>Красным шрифтом в ячейке даты</b> выделены даты заключения
  договоров, приходящиеся на выходные или нерабочие праздничные
  (перенесённые) дни.
  <br>
  <b>Констатация ОГРН.</b> ОГРН, указанный рядом с наименованием
  организации, — это тот ОГРН, по которому производился поиск
  в ГИС ЖКХ, а не утверждение соответствия данного ЮЛ этому ОГРН.
  ГИС ЖКХ в отдельных случаях может отдавать по одному ОГРН карточки,
  ссылающиеся на другое ЮЛ. Мы констатируем результат, полученный
  от ГИС ЖКХ на момент прогона.
</div>

<h2>Сводка по УК</h2>

<button id="btn-expand-all" style="padding: 6px 14px; cursor: pointer;
        border: 1px solid #888; border-radius: 4px;
        background: #f0f0f0; font-size: 13px;">Развернуть все</button>

<table class="main-table" id="tbl">
  <thead>
    <tr>
      <th class="no-sort"></th>
      <th data-key="trust">УК / ОГРН / форма / % доверия<span class="arrow"></span></th>
      <th data-key="addr">Адресов<span class="arrow"></span></th>
      <th data-key="mass">Массовая дата<span class="arrow"></span></th>
      <th data-key="markers">Маркеры<span class="arrow"></span></th>
      <th class="no-sort">Аномалии</th>
      <th class="no-sort">Протокол</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div class="legal">
  <h2>Дисклеймер</h2>
  <p>
    Отчёт сформирован автоматически по результатам программного опроса открытых
    источников — портала ГИС ЖКХ. Единственный источник данных — ГИС ЖКХ на момент
    прогона. Отчёт не является юридическим заключением и не заменяет проверку,
    проводимую уполномоченными органами.
  </p>
  <p>
    При подготовке отчёта использовались инструменты на базе искусственного
    интеллекта. Расчёты площадей, кворумов и иных величин носят оценочный характер.
  </p>

  <h2>Вилка решений</h2>
  <p>
    <b>Вариант А.</b> Устранение выявленных недостатков: размещение достоверных
    сведений, актуализация сведений об ЭП, повторная публикация.<br>
    <b>Вариант Б.</b> Отказ от устранения и принятие связанных с этим последствий,
    включая предусмотренные законом меры реагирования.<br>
    <b>Вариант В.</b> Игнорирование выявленных фактов и их последующая публикация
    в открытых источниках.
  </p>

  <h2>Ссылки</h2>
  <p>
    Лог ошибок: <a href="file:///__DL_ERRORS_PATH__">download_errors.log</a><br>
    Перечень НПА: <a href="file:///__NPA_LIST_PATH__">npa_list.txt</a>
  </p>
</div>

<div id="overlay">
  <div class="frame" id="ov_frame">
    <div class="close" onclick="closeOverlay()">×</div>
    <div class="paused-badge">ПАУЗА</div>
    <img id="ov_img0" alt="">
    <img id="ov_img1" alt="">
    <img id="ov_img2" alt="">
    <img id="ov_img3" alt="">
    <div class="caption" id="ov_caption"></div>
  </div>
</div>

<div class="tip" id="tip"></div>

<script>
const DATA = __DATA__;
const ORG_SUMMARY = __ORG_SUMMARY__;
const TIPS_BY_FIO = __TIPS_BY_FIO__;
const TIPS_BY_ORG = __TIPS_BY_ORG__;
const ORG_LIST = __ORG_LIST__;
const FIO_LIST = __FIO_LIST__;
const TECH_STATE = __TECH_STATE__;

let expandedOrgs = new Set();
let sortKey = "trust";
let sortAsc = true;
let slideshowTimer = null;
const FRAME_MS = 2000;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[c]));
}
function fmtNum(v) {
  if (v == null) return "";
  const n = Number(v);
  if (!isFinite(n)) return "";
  return n.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}
function fmtNegDelta(v) {
  if (v == null) return "";
  const n = Number(v);
  if (!(n < 0)) return "";
  return "\u2212" + Math.abs(n).toFixed(2).replace(/\.?0+$/, "") + " м<sup>2</sup>";
}
function trustColor(t) {
  if (t >= 99.5) return "hsl(200, 70%, 50%)";
  if (t >= 81)   return "hsl(140, 60%, 40%)";
  if (t >= 61)   return "hsl(80, 65%, 40%)";
  if (t >= 41)   return "hsl(50, 80%, 40%)";
  if (t >= 21)   return "hsl(30, 85%, 45%)";
  if (t >= 0.5)  return "hsl(10, 80%, 45%)";
  return "hsl(350, 70%, 35%)";
}
function hashCode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function pastelColorForDate(d) {
  const h = hashCode(d) % 360;
  return "hsl(" + h + ", 50%, 92%)";
}
function massDateKey(s) {
  return (s && s.mass_date) || "";
}
function markersSum(s) {
  if (!s) return 0;
  return (s.n_count || 0) + (s.j_count || 0) + (s.e_count || 0) + (s.o_count || 0);
}

function render() {
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";

  const byOrg = {};
  DATA.forEach(r => {
    const key = r.org_norm;
    if (!byOrg[key]) byOrg[key] = [];
    byOrg[key].push(r);
  });

  const orgs = Object.keys(byOrg);
  orgs.sort((a, b) => {
    const sa = ORG_SUMMARY[a] || {};
    const sb = ORG_SUMMARY[b] || {};
    let va, vb;
    if (sortKey === "trust") {
      va = sa.trust || 0; vb = sb.trust || 0;
    } else if (sortKey === "addr") {
      va = sa.addr_count || 0; vb = sb.addr_count || 0;
    } else if (sortKey === "mass") {
      va = massDateKey(sa); vb = massDateKey(sb);
    } else if (sortKey === "markers") {
      va = markersSum(sa); vb = markersSum(sb);
    } else {
      va = 0; vb = 0;
    }
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return a.localeCompare(b);
  });

  orgs.forEach(orgKey => {
    const s = ORG_SUMMARY[orgKey] || {};
    const color = trustColor(s.trust || 0);
    const ogrn = (s.ogrns && s.ogrns[0]) || "";
    const form = (s.org_ogrn_egrul && ORG_LIST[s.org_ogrn_egrul] && ORG_LIST[s.org_ogrn_egrul].form) || "";
    const displayName = s.org || orgKey;

    let nameHtml = esc(displayName);
    if (ogrn) {
      const tipKey = s.org_ogrn_egrul || ogrn;
      const hasTip = TIPS_BY_ORG && TIPS_BY_ORG[tipKey];
      const cls = hasTip ? "org-tip" : "";
      nameHtml = `<span class="${cls}" data-org="${esc(tipKey)}">${esc(displayName)}</span>`;
    }
    const orgCell =
      `<td class="org-cell">` +
        `<span class="org-name">${nameHtml}</span>` +
        (ogrn ? `<span class="org-ogrn">${esc(form ? form + " " : "")}${esc(ogrn)}</span>` : "") +
        `<span class="org-score" style="color:${color}">${(s.trust || 0).toFixed(1)}%</span>` +
      `</td>`;

    let addrCell;
    if ((s.neg_count || 0) > 0) {
      const sumTxt = fmtNegDelta(s.neg_sum);
      addrCell =
        `<td class="addr-cell">` +
          `<span class="addr-text">${s.addr_count || 0}</span>` +
          `<span class="addr-delta">${sumTxt}</span>` +
        `</td>`;
    } else {
      addrCell = `<td>${s.addr_count || 0}</td>`;
    }

    let dateCell = `<td></td>`;
    if (s.mass_date) {
      const bg = s.mass_joke ? "#FFE0E0" : pastelColorForDate(s.mass_date);
      dateCell =
        `<td class="mass-date" style="background:${bg}">` +
          `${esc(s.mass_date)} <b>(${s.mass_count}×)</b>` +
          `<span class="mass-hint">проверить кворумы и пролонгацию</span>` +
        `</td>`;
    }

    let counters = [];
    if (s.n_count) counters.push(`<span class="n-counter">Н/Д: ${s.n_count}</span>`);
    if (s.j_count) counters.push(`<span class="j-counter">Н/В: ${s.j_count}</span>`);
    if (s.e_count) counters.push(`<span class="e-counter">Н/Э: ${s.e_count}</span>`);
    if (s.o_count) counters.push(`<span class="o-counter">Н/О: ${s.o_count}</span>`);
    const markersCell = `<td>${counters.join("")}</td>`;

    let anomalies = [];
    if (s.ghost_uk) anomalies.push("GHOST_UK");
    if (s.low_capital) anomalies.push("LOW_CAPITAL");
    if (s.mass_joke) anomalies.push("MASS_JOKE_DATE");
    const anomaliesCell = `<td>${anomalies.join(", ")}</td>`;

    const viewCell = `<td></td>`;

    const tr = document.createElement("tr");
    tr.className = "org-row";
    tr.innerHTML =
      `<td class="toggle-cell">${expandedOrgs.has(orgKey) ? "−" : "+"}</td>` +
      orgCell + addrCell + dateCell + markersCell + anomaliesCell + viewCell;
    tbody.appendChild(tr);

    const houses = byOrg[orgKey].slice().sort((a, b) => (a.addr || "").localeCompare(b.addr || ""));
    houses.forEach(r => {
      const markerCls = r.marker === "Ж" ? "marker-zh"
                      : r.marker === "Н" ? "marker-n"
                      : r.marker === "Д" ? "marker-d"
                      : "marker-other";
      const mass = s.mass_date && r.date === s.mass_date;
      const dateCls = [mass ? "mass-date" : "", r.is_holiday ? "holiday-date" : ""].filter(Boolean).join(" ");
      const dateStyle = mass ? ` style="background:${pastelColorForDate(r.date)}"` : "";

      let addrHtml;
      if (r.delta_value != null && r.delta_value < 0) {
        addrHtml =
          `<td class="addr-cell house-addr">` +
            `<span class="addr-text">${esc(r.addr)}</span>` +
            `<span class="addr-delta">${fmtNegDelta(r.delta_value)}</span>` +
          `</td>`;
      } else {
        addrHtml = `<td class="house-addr">${esc(r.addr)}</td>`;
      }

      let anom = [];
      if (r.delta_value != null && r.delta_value < 0) anom.push("Δ<0");
      const anomCell = `<td>${anom.join(", ")}</td>`;

      const viewHtml = r.has_real_screens
        ? `<span class="btn-view">Смотреть</span>`
        : "";

      const htr = document.createElement("tr");
      htr.className = "house-row" + (expandedOrgs.has(orgKey) ? " visible" : "");
      htr.innerHTML =
        `<td></td>` +
        `<td></td>` +
        addrHtml +
        `<td class="${dateCls}"${dateStyle}>${esc(r.date)}</td>` +
        `<td class="${markerCls}">${esc(r.marker_long)}</td>` +
        anomCell +
        `<td>${viewHtml}</td>`;
      const btn = htr.querySelector(".btn-view");
      if (btn) {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          openOverlay(r);
        });
      }
      tbody.appendChild(htr);
    });

    const toggle = tr.querySelector(".toggle-cell");
    toggle.addEventListener("click", () => {
      const nowOpen = !expandedOrgs.has(orgKey);
      if (nowOpen) expandedOrgs.add(orgKey);
      else expandedOrgs.delete(orgKey);
      render();
    });
  });

  document.getElementById("count").textContent = DATA.length;

  document.querySelectorAll(".org-tip").forEach(el => {
    el.addEventListener("mouseenter", (ev) => {
      const ogrn = el.dataset.org;
      const tipLines = TIPS_BY_ORG[ogrn];
      if (tipLines) showTip(tipLines, ev.clientX, ev.clientY);
    });
    el.addEventListener("mousemove", (ev) => positionTip(ev.clientX, ev.clientY));
    el.addEventListener("mouseleave", hideTip);
  });

  document.querySelectorAll("th[data-key]").forEach(th => {
    const arrow = th.querySelector(".arrow");
    if (arrow) arrow.textContent = (th.dataset.key === sortKey) ? (sortAsc ? "▲" : "▼") : "";
  });
}

function openOverlay(record) {
  const ov = document.getElementById("overlay");
  const imgs = [
    document.getElementById("ov_img0"),
    document.getElementById("ov_img1"),
    document.getElementById("ov_img2"),
    document.getElementById("ov_img3"),
  ];
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }

  const srcs = (record.screens || []).slice();
  imgs.forEach(im => {
    im.classList.remove("active");
    im.removeAttribute("src");
    im.style.display = "none";
  });
  srcs.forEach((src, i) => {
    if (i >= imgs.length) return;
    imgs[i].src = src;
    imgs[i].style.display = "";
  });
  imgs[0].classList.add("active");

  ov.classList.add("open");
  ov.classList.remove("paused");
  const state = { imgs, cur: 0, paused: false, n: Math.min(srcs.length, imgs.length) };

  slideshowTimer = setInterval(() => {
    if (state.paused) return;
    if (state.cur >= state.n - 1) {
      clearInterval(slideshowTimer);
      slideshowTimer = null;
      closeOverlay();
      return;
    }
    state.imgs[state.cur].classList.remove("active");
    state.cur += 1;
    state.imgs[state.cur].classList.add("active");
  }, FRAME_MS);
}

function closeOverlay() {
  const ov = document.getElementById("overlay");
  ov.classList.remove("open");
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeOverlay();
});

function positionTip(x, y) {
  const tipEl = document.getElementById("tip");
  const rect = tipEl.getBoundingClientRect();
  const pad = 12;
  let left = x + 16;
  let top  = y + 16;
  if (left + rect.width + pad > window.innerWidth)  left = x - rect.width - 16;
  if (top  + rect.height + pad > window.innerHeight) top  = y - rect.height - 16;
  if (left < pad) left = pad;
  if (top  < pad) top  = pad;
  tipEl.style.left = left + "px";
  tipEl.style.top  = top  + "px";
}

function showTip(lines, x, y) {
  const tipEl = document.getElementById("tip");
  const html = [];
  for (const ln of lines) {
    if (ln.type === "title") html.push(`<div class="title">${esc(ln.text)}</div>`);
    else if (ln.type === "sect") html.push(`<div class="sect">${esc(ln.text)}</div>`);
    else if (ln.type === "who") html.push(`<div class="who">${esc(ln.text)}</div>`);
    else html.push(`<div class="line">${esc(ln.text)}</div>`);
  }
  tipEl.innerHTML = html.join("");
  tipEl.classList.add("open");
  positionTip(x, y);
}
function hideTip() {
  document.getElementById("tip").classList.remove("open");
}

document.getElementById("btn-expand-all").addEventListener("click", () => {
  const allOrgs = Object.keys(ORG_SUMMARY);
  const allOpen = allOrgs.every(o => expandedOrgs.has(o));
  if (allOpen) expandedOrgs.clear();
  else allOrgs.forEach(o => expandedOrgs.add(o));
  document.getElementById("btn-expand-all").textContent = allOpen ? "Развернуть все" : "Свернуть все";
  render();
});

document.querySelectorAll("th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = true; }
    render();
  });
});

render();
</script>
</body>
</html>
"""


# ============================================================
# СБОРКА ТУЛТИПОВ
# ============================================================
def role_label_for(egrul: Egrul, ogrn: str, inn: str) -> str:
    e = egrul.entities[ogrn]
    is_dir = (e.get("director_inn") or "") == inn
    is_f = any((f.get("inn") or "") == inn for f in (e.get("founders") or []))
    if is_dir and is_f:
        return "директор, учредитель"
    if is_dir:
        return "директор"
    return "учредитель"


def build_fio_tooltip(egrul: Egrul, fio: str, inn: str) -> list[dict]:
    lines = [{"type": "title", "text": "Предположительный граф аффилированности"}]
    orgs: set[str] = set()
    orgs.update(egrul.directors_by_inn.get(inn, []))
    orgs.update(egrul.founders_by_inn.get(inn, []))
    sorted_orgs = sorted(orgs, key=lambda o: egrul.display_name(o))
    for i, ogrn in enumerate(sorted_orgs):
        last = i == len(sorted_orgs) - 1
        tree = "└──" if last else "├──"
        name = egrul.display_name(ogrn)
        form = egrul.display_form(ogrn)
        lines.append({"type": "line",
                      "text": f"{tree} {form} \"{name}\" ({ogrn}) — {role_label_for(egrul, ogrn, inn)}"})
    return lines


def build_org_tooltip(egrul: Egrul, ogrn: str) -> list[dict] | None:
    lines = [{"type": "title", "text": "Предположительный граф аффилированности"}]

    nbr = egrul.neighbors_by_person(ogrn)
    if nbr:
        lines.append({"type": "sect", "text": "Связи через персон:"})
        nbr_items = sorted(nbr.items(), key=lambda kv: egrul.display_name(kv[0]))
        for i, (nb_ogrn, item) in enumerate(nbr_items):
            last = i == len(nbr_items) - 1
            tree = "└──" if last else "├──"
            name = egrul.display_name(nb_ogrn)
            form = egrul.display_form(nb_ogrn)
            lines.append({"type": "line", "text": f"  {tree} {form} \"{name}\" ({nb_ogrn})"})
            for p in item["persons"]:
                lines.append({"type": "who",
                              "text": f"        через {p['fio']} ({p['inn']}) — {p['role']}"})

    addr = egrul.address_neighbors(ogrn)
    if addr:
        kind = "жилой адрес" if addr["kind"] == "home" else "офис"
        lines.append({"type": "sect", "text": "По «резиновому» офису:"})
        lines.append({"type": "line", "text": f"  {addr['display']} ({kind})"})
        sorted_ogrns = sorted(addr["ogrns"], key=lambda o: egrul.display_name(o))
        for i, nb_ogrn in enumerate(sorted_ogrns):
            last = i == len(sorted_ogrns) - 1
            tree = "└──" if last else "├──"
            name = egrul.display_name(nb_ogrn)
            form = egrul.display_form(nb_ogrn)
            lines.append({"type": "line", "text": f"  {tree} {form} \"{name}\" ({nb_ogrn})"})

    if len(lines) == 1:
        return None
    return lines


def build_fio_list(egrul: Egrul, min_orgs: int = 2) -> list[dict]:
    fio_map: dict[str, dict] = {}
    for inn, ogrns in egrul.directors_by_inn.items():
        fio = None
        for o in ogrns:
            e = egrul.entities[o]
            if (e.get("director_inn") or "") == inn:
                fio = norm_fio(e.get("director") or "")
                break
        if not fio:
            continue
        fio_map.setdefault(inn, {"fio": fio, "inn": inn, "ogrns": set()})
        fio_map[inn]["ogrns"].update(ogrns)
    for inn, ogrns in egrul.founders_by_inn.items():
        fio = None
        for o in ogrns:
            e = egrul.entities[o]
            for f in e.get("founders") or []:
                if (f.get("inn") or "") == inn:
                    fio = norm_fio(f.get("name") or "")
                    break
            if fio:
                break
        if not fio:
            continue
        fio_map.setdefault(inn, {"fio": fio, "inn": inn, "ogrns": set()})
        fio_map[inn]["ogrns"].update(ogrns)
    out = [
        {"fio": v["fio"], "inn": v["inn"], "ogrns": sorted(v["ogrns"])}
        for v in fio_map.values()
        if len(v["ogrns"]) >= min_orgs
    ]
    out.sort(key=lambda x: x["fio"])
    return out


# ============================================================
# ОЧИСТКА PNG
# ============================================================
def trash_unused_png(records: list[dict], dry_run: bool = False) -> None:
    used: set[str] = set()
    for r in records:
        for s in r.get("screens") or []:
            if s and s.endswith(".png"):
                used.add(s)

    all_png = sorted(IMG_DIR.glob("*.png"), key=lambda p: p.name)
    unused = [p for p in all_png if p.name not in used]

    print(f"[+] использовано скринов: {len(used)}")
    print(f"[+] всего .png в IMG_DIR: {len(all_png)}")
    print(f"[+] неиспользованных: {len(unused)}")

    if dry_run or not unused:
        return
    try:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[!] не удалось создать {TRASH_DIR}: {e}")
        return
    moved = 0
    for p in unused:
        try:
            target = TRASH_DIR / p.name
            if target.exists():
                continue
            shutil.move(str(p), str(target))
            moved += 1
        except Exception as e:
            print(f"    [!] {p.name}: {e}")
    print(f"[+] перемещено в {TRASH_DIR}: {moved}")


# ============================================================
# MAIN
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-trash", action="store_true", help="не чистить PNG")
    ap.add_argument("--only-ogrn", default="", help="обработать только один ОГРН")
    ap.add_argument("--no-egrul", action="store_true", help="не загружать ЕГРЮЛ")
    args = ap.parse_args()

    if not LOG_DIR.exists():
        print(f"[!] нет папки {LOG_DIR}")
        return

    print("[i] загрузка ЕГРЮЛ...")
    egrul = None
    if not args.no_egrul:
        try:
            egrul = load_egrul(filter_by_act=True)
            print(f"[+] ЕГРЮЛ: {len(egrul.entities)} записей")
        except Exception as e:
            print(f"[!] ЕГРЮЛ не загружен: {e}")
            traceback.print_exc()

    print("[i] парсинг contracts*.log...")
    records = parse_contracts_logs(egrul)
    if args.only_ogrn:
        records = [r for r in records if r["ogrn"] == args.only_ogrn]
    print(f"[+] записей contracts: {len(records)}")

    print("[i] парсинг gisjkh-ufo*.log...")
    gisjkh_stats = parse_gisjkh_logs()
    if args.only_ogrn:
        gisjkh_stats = {k: v for k, v in gisjkh_stats.items() if k == args.only_ogrn}
    print(f"[+] ОГРН в статистике gisjkh: {len(gisjkh_stats)}")

    print("[i] массовые даты...")
    mass_dates = compute_mass_dates(records)
    print(f"[+] УК с массовой датой: {len(mass_dates)}")

    print("[i] сводка по УК...")
    org_summary = compute_org_summary(records, egrul, gisjkh_stats, mass_dates)
    print(f"[+] УК в сводке: {len(org_summary)}")

    tech_by_marker: dict[str, int] = defaultdict(int)
    fallback_ok = 0
    tech_houses = 0
    for ogrn, gs in gisjkh_stats.items():
        t = sum(gs["tech"].values())
        if t:
            tech_houses += 1
        for k, v in gs["tech"].items():
            tech_by_marker[k] += v
        fallback_ok += gs.get("fallback_ok", 0)

    total_houses = len(records)
    tech_pct = (tech_houses / total_houses * 100) if total_houses else 0
    tech_state = {
        "total_ogrns": len(gisjkh_stats),
        "total_houses": total_houses,
        "tech_houses": tech_houses,
        "tech_pct": round(tech_pct, 2),
        "fallback_ok": fallback_ok,
        "by_marker": dict(tech_by_marker),
    }

    tips_by_fio = {}
    tips_by_org = {}
    fio_list = []
    org_list = {}

    if egrul:
        print("[i] тултипы на ФИО...")
        fio_list = build_fio_list(egrul, min_orgs=2)
        for f in fio_list:
            tips_by_fio[f["inn"]] = build_fio_tooltip(egrul, f["fio"], f["inn"])

        print("[i] тултипы на ЮЛ...")
        for ogrn in egrul.entities:
            tip = build_org_tooltip(egrul, ogrn)
            if tip:
                tips_by_org[ogrn] = tip

        for ogrn, e in egrul.entities.items():
            org_list[ogrn] = {
                "ogrn": ogrn,
                "name": egrul.display_name(ogrn),
                "form": egrul.display_form(ogrn),
            }

    data_js = json.dumps(records, ensure_ascii=False)
    summary_js = json.dumps(
        {k: {**v, "ogrns": sorted(v.get("ogrns") or [])} for k, v in org_summary.items()},
        ensure_ascii=False,
    )
    tips_fio_js = json.dumps(tips_by_fio, ensure_ascii=False)
    tips_org_js = json.dumps(tips_by_org, ensure_ascii=False)
    org_list_js = json.dumps(org_list, ensure_ascii=False)
    fio_list_js = json.dumps([{"fio": f["fio"], "inn": f["inn"]} for f in fio_list], ensure_ascii=False)
    tech_js = json.dumps(tech_state, ensure_ascii=False)

    npa_path = NPA_LIST.resolve().as_posix()
    dl_path = DL_ERRORS.resolve().as_posix()

    html = (HTML_TEMPLATE
            .replace("__DATA__", data_js)
            .replace("__ORG_SUMMARY__", summary_js)
            .replace("__TIPS_BY_FIO__", tips_fio_js)
            .replace("__TIPS_BY_ORG__", tips_org_js)
            .replace("__ORG_LIST__", org_list_js)
            .replace("__FIO_LIST__", fio_list_js)
            .replace("__TECH_STATE__", tech_js)
            .replace("__NPA_LIST_PATH__", npa_path)
            .replace("__DL_ERRORS_PATH__", dl_path))

    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"[+] report: {OUT_HTML}")

    NPA_LIST.write_text(NPA_TEXT, encoding="utf-8")
    print(f"[+] npa_list: {NPA_LIST}")

    DL_ERRORS.write_text(DL_HEADER, encoding="utf-8")
    print(f"[+] download_errors: {DL_ERRORS}")

    if not args.no_trash:
        trash_unused_png(records)
    else:
        print("[i] очистка PNG пропущена (--no-trash)")


if __name__ == "__main__":
    main()
