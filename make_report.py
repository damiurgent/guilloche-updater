# -*- coding: utf-8 -*-
"""
make_report.py — генератор HTML-отчёта по проверке ЭП договоров УК.

Источники (все читаются "склеенными"):
  1) %TEMP%\\IN\\LOG\\contracts*.log  — таблица нарушителей (Н / Ж)
                                        с 3 или 4 скриншотами на дом.
  2) %TEMP%\\IN\\LOG\\gisjkh-ufo*.log — общий лог проверок.

Уровень доверия по УК = Д / (Д + Н + Ж) × 100%.
Цвет шрифта — по тепловой шкале (голубой → бордовый).

Структура отчёта — дерево:
    строка-УК (свёрнуто) + раскрывающиеся строки-дома.

Свёрнутая строка УК:
    1) Организация + trust
    2) Адрес: число адресов (слева) + сводка по отрицательным Δ (справа)
    3) Дата ДУ — только если есть массовая дата
    4) Результат ЭП — Н/Д + Н/Д, Н/В
    5) Протокол — пусто

Строка дома:
    Адрес: адрес (слева) + отрицательная Δ красным (справа), если есть
    Дата — красным, если выходной/праздник; пастельный фон, если массовая
    Результат / Просмотр

Сортировка: по trust ↑ (по возрастанию), tie — по имени УК.
Слайдшоу: 3 или 4 кадра. Пауза/продолжение: пробел или тап.
"""

import re
import json
import shutil
from datetime import date as _date
from pathlib import Path
from tempfile import gettempdir

# ============================== ПРАЗДНИКИ ==============================
try:
    import holidays as _holidays
    RU_HOLIDAYS = _holidays.RU(years=range(2000, 2032))
    _HAS_HOLIDAYS = True
except Exception as _e:
    RU_HOLIDAYS = None
    _HAS_HOLIDAYS = False
    print(f"[!] holidays не установлен или ошибка: {_e}")
    print("[i] будет учитываться только суббота/воскресенье")

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
LOG_DIR = BASE_DIR / "LOG"
JPG_DIR = BASE_DIR / "JPG"
OUT_HTML = JPG_DIR / "report.html"

# Куда сносить неиспользуемые JPG (вне report).
TRASH_DIR = Path(r"D:\IN\OLD\JPG")

# ============================== НАСТРОЙКИ ==============================
MARKER_LONG = {
    "Н": "Подпись верна, но НЕДЕЙСТВИТЕЛЬНА",
    "Ж": "Подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА",
    "Д": "Подпись ДЕЙСТВИТЕЛЬНА",
}

MASS_DATE_MIN = 2
MASS_DATE_HINT = ("проверить кворумы МКД по бюллетеням "
                  "и законность пролонгации")

# ============================== ПАРСИНГ contracts*.log ==============================
RE_LINE1 = re.compile(
    r"^(scr\d{4}\.jpg)\s*-\s*(.+?)\s*--\s*(.+?)\s*---\s*(.+?)\s*----\s*(.+)$"
)
RE_LINE2 = re.compile(r"^(scr\d{4}\.jpg)\s*-\s*(.+?)\s*--\s*(.+)$")
RE_LINE3 = re.compile(r"^(scr\d{4}\.jpg)\s*-\s*(.+)$")
RE_LINE4_SCR = re.compile(r"^(scr\d{4}\.jpg)\s*$")

RE_DELTA_VALUE = re.compile(r"=\s*(-?\d+(?:[.,]\d+)?)\s*$")
RE_DATE_DDMMYYYY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def _iter_contract_files():
    files = sorted(LOG_DIR.glob("contracts*.log"), key=lambda p: p.name)
    return files


def _extract_delta_value(pdf_again: str):
    """
    pdf_again — то, что во второй строке между ' - ' и ' -- '
    (например '(714.9)-720.3=-5.4' или 'dogmol2.pdf').
    Возвращает float или None.
    """
    if not pdf_again:
        return None
    m = RE_DELTA_VALUE.search(pdf_again.strip())
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def _is_holiday(date_str: str) -> bool:
    """
    True, если дата 'dd.mm.yyyy' — суббота, воскресенье или
    нерабочий праздничный/перенесённый день (по производственному
    календарю РФ).
    """
    if not date_str:
        return False
    m = RE_DATE_DDMMYYYY.match(date_str.strip())
    if not m:
        return False
    try:
        d = _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except Exception:
        return False

    # Праздники (через библиотеку) — если есть
    if _HAS_HOLIDAYS and RU_HOLIDAYS is not None:
        try:
            if d in RU_HOLIDAYS:
                return True
        except Exception:
            pass

    # Сб/Вс — всегда нерабочие (перенесённые рабочие субботы
    # библиотека holidays тоже учитывает: если рабочая суббота
    # объявлена рабочей, её нет в RU_HOLIDAYS — но weekday() == 5.
    # Чтобы не подсвечивать рабочие субботы, проверяем:
    #  - если в RU_HOLIDAYS — да;
    #  - иначе, если это сб/вс и НЕ в списке рабочих суббот — да.
    # Простой способ: сб/вс, кроме тех, что явно рабочие.
    # В РФ перенесённые рабочие субботы объявляются постановлением.
    # Библиотека holidays их не помечает как рабочие — их просто нет
    # в списке праздников. Поэтому для сб/вс используем weekday,
    # но исключаем те, что попадают в известные переносы.
    if d.weekday() >= 5:
        # Если в списке праздников — уже отдали True выше.
        # Иначе — сб/вс; в большинстве случаев это выходной.
        return True

    return False


def parse_contracts_logs():
    """
    Читает все contracts*.log и склеивает их в один список записей.
    Блок = 3 или 4 непустые строки, разделённые пустой строкой.

    Дедупликация: если в разных файлах встречается один и тот же
    дом (org + addr + date) — оставляем только первую запись.
    Это спасает от двойного прогона одного и того же ОГРН.
    """
    records = []
    seen_keys = set()

    for path in _iter_contract_files():
        text = path.read_text(encoding="utf-8")
        blocks = re.split(r"\r?\n\r?\n", text.strip())
        for block in blocks:
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
            if len(lines) not in (3, 4):
                continue

            m1 = RE_LINE1.match(lines[0].strip())
            if not m1:
                continue
            scr_a, org, addr, pdf_name, date_str = m1.groups()

            m2 = RE_LINE2.match(lines[1].strip())
            if not m2:
                continue
            scr_b, _pdf_again, ep_name = m2.groups()

            m3 = RE_LINE3.match(lines[2].strip())
            if not m3:
                continue
            scr_c, marker = m3.groups()

            scr_pdf = ""
            if len(lines) == 4:
                m4 = RE_LINE4_SCR.match(lines[3].strip())
                if m4:
                    scr_pdf = m4.group(1)

            delta_value = _extract_delta_value(_pdf_again)
            hol = _is_holiday(date_str)

            # --- дедупликация: один и тот же дом по (org, addr, date) ---
            key = (
                (org or "").strip().lower(),
                (addr or "").strip().lower(),
                (date_str or "").strip(),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)

            records.append({
                "src": path.name,
                "scr_a": scr_a,
                "org": org,
                "addr": addr,
                "pdf_name": pdf_name,
                "date": date_str,
                "scr_b": scr_b,
                "ep_name": ep_name,
                "scr_c": scr_c,
                "marker": marker,
                "marker_long": MARKER_LONG.get(marker, marker),
                "scr_pdf": scr_pdf,
                "delta_value": delta_value,
                "is_holiday": hol,
            })
    return records

# ============================== ПАРСИНГ gisjkh-ufo*.log ==============================
RE_HOUSE_INFO = re.compile(
    r"house_info:\s+"
    r"org='([^']*)'\s+"
    r"addr='([^']*)'\s+"
    r"pdf='([^']*)'\s+"
    r"date='([^']*)'"
)
RE_RESULT_MARKER = re.compile(
    r"result_text:\s*'([^']*)'\s+marker:\s*'([^']*)'"
)
RE_RESULT_NONE = re.compile(
    r"result_text:\s*'ДЕЙСТВИТЕЛЬНА'\s+marker:\s*None"
)

def _iter_gisjkh_files():
    files = sorted(LOG_DIR.glob("gisjkh-ufo*.log"), key=lambda p: p.name)
    return files


def parse_gisjkh_logs():
    stats = {}
    for path in _iter_gisjkh_files():
        current_org = None
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m_hi = RE_HOUSE_INFO.search(line)
                if m_hi:
                    current_org = m_hi.group(1).strip()
                    continue

                m_res = RE_RESULT_MARKER.search(line)
                if m_res and current_org:
                    _txt, marker = m_res.group(1).strip(), m_res.group(2).strip()
                    if marker in ("Н", "Ж"):
                        _bump(stats, current_org, marker)
                    current_org = None
                    continue

                if RE_RESULT_NONE.search(line) and current_org:
                    _bump(stats, current_org, "Д")
                    current_org = None
                    continue
    return stats


def _bump(stats: dict, org: str, marker: str):
    if org not in stats:
        stats[org] = {"total": 0, "Д": 0, "Н": 0, "Ж": 0}
    stats[org]["total"] += 1
    stats[org][marker] = stats[org].get(marker, 0) + 1

# ============================== МАССОВЫЕ ДАТЫ ==============================
def compute_mass_date_per_org(records):
    counts_per_org = {}
    order_per_org = {}
    for r in records:
        org = r["org"]
        date = r["date"]
        if org not in counts_per_org:
            counts_per_org[org] = {}
            order_per_org[org] = []
        if date not in counts_per_org[org]:
            counts_per_org[org][date] = 0
            order_per_org[org].append(date)
        counts_per_org[org][date] += 1

    result = {}
    for org, counts in counts_per_org.items():
        mass = [(d, c) for d, c in counts.items() if c >= MASS_DATE_MIN]
        if not mass:
            continue
        max_count = max(c for _, c in mass)
        for d in order_per_org[org]:
            if counts.get(d) == max_count and counts[d] >= MASS_DATE_MIN:
                result[org] = {"date": d, "count": max_count}
                break
    return result

# ============================== СВОДКА ПО УК ==============================
def compute_org_summary(records, org_stats, mass_per_org):
    summary = {}
    for r in records:
        org = r["org"]
        if org not in summary:
            summary[org] = {
                "org": org,
                "addr_count": 0,
                "n_count": 0,
                "j_count": 0,
                "mass_date": "",
                "mass_count": 0,
                "trust": 0.0,
                "neg_count": 0,
                "neg_sum": 0.0,
                "holiday_count": 0,
            }
        s = summary[org]
        s["addr_count"] += 1
        if r["marker"] == "Н":
            s["n_count"] += 1
        elif r["marker"] == "Ж":
            s["j_count"] += 1

        dv = r.get("delta_value")
        if dv is not None and dv < 0:
            s["neg_count"] += 1
            s["neg_sum"] += dv

        if r.get("is_holiday"):
            s["holiday_count"] += 1

    for org, s in summary.items():
        backend = org_stats.get(org)
        if backend and backend.get("total", 0) > 0:
            d = backend.get("Д", 0)
            s["trust"] = d / backend["total"] * 100
        else:
            s["trust"] = 0.0

        mass = mass_per_org.get(org)
        if mass:
            s["mass_date"] = mass["date"]
            s["mass_count"] = mass["count"]

    return summary

# ============================== HTML-ШАБЛОН ==============================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Отчёт по проверке ЭП договоров УК</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         margin: 20px; background: #f5f5f5; color: #222; }
  h1 { font-size: 20px; margin-bottom: 10px; }
  .meta { color: #666; font-size: 13px; margin-bottom: 16px; line-height: 1.5; }
  table { border-collapse: collapse; background: #fff; width: 100%;
          box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
  th, td { border: 1px solid #ddd; padding: 6px 8px; font-size: 13px;
           vertical-align: top; }
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

  td.org-cell .org-name {
      display: block; text-align: left; font-weight: 600;
  }
  td.org-cell .org-score {
      display: block; text-align: right; font-weight: 700;
      font-variant-numeric: tabular-nums;
  }

  td.toggle-cell {
      text-align: center; vertical-align: middle;
      cursor: pointer; user-select: none;
      font-weight: bold; font-size: 16px;
      width: 28px;
  }
  td.toggle-cell:hover { background: #dcdcdc; }

  td.mass-date { font-weight: 600; }
  td.mass-date .mass-hint {
      display: block; font-weight: 400; font-size: 11px;
      color: #555; margin-top: 2px;
  }

  /* дата-выходной/праздник — красным */
  td.holiday-date {
      color: #b00; font-weight: bold;
  }

  td.marker-zh  { color: #b00; font-weight: bold; }
  td.marker-n   { color: #d68000; font-weight: bold; }
  td.marker-other { color: #333; font-weight: bold; }

  .n-counter { color: #d68000; font-weight: bold; }
  .j-counter { color: #b00; font-weight: bold; margin-left: 14px; }

  /* ячейка адреса с Δ справа */
  td.addr-cell {
      display: flex; justify-content: space-between;
      align-items: flex-start; gap: 12px;
  }
  td.addr-cell .addr-text { flex: 1 1 auto; }
  td.addr-cell .addr-delta {
      flex: 0 0 auto; color: #b00; font-weight: bold;
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
</style>
</head>
<body>
<h1>Отчёт по проверке квалифицированной ЭП договоров УК</h1>
<div style="margin-bottom:8px;">
  <button id="btn-expand-all" style="
      padding: 6px 14px; cursor: pointer;
      border: 1px solid #888; border-radius: 4px;
      background: #f0f0f0; font-size: 13px;
  ">Развернуть все</button>
</div>
<div class="meta">
  Записей: <span id="count">0</span>.
  <br>
  <b>Уровень доверия</b> по УК = Д / (Д + Н/Д + Н/Д, Н/В) × 100%,
  где Д — дома с действительной ЭП, Н/Д — подпись верна, но недействительна,
  Н/Д, Н/В — подпись неверна и недействительна. Цвет — по тепловой шкале
  (голубой = высокий, бордовый = низкий).
  Сортировка по умолчанию — по уровню доверия <b>↑</b> (аутсайдеры сверху).
  <br>
  <b>Массовые даты</b> (у одной УК, ≥<span id="mass_min">2</span> раз)
  показаны в свёрнутой строке УК — берётся дата с максимальным
  числом повторов. Подсвечены пастельным цветом.
  <br>
  <b>Отрицательная площадь</b> (красным справа от адреса) —
  разница между общей площадью дома и площадью жилых помещений,
  если она получилась меньше нуля. Показывается как
  <code>−N м<sup>2</sup></code> и только для таких домов.
  <br>
  <b>Красным шрифтом в ячейке даты</b> выделены даты заключения
  договоров, приходящиеся на выходные или нерабочие праздничные
  (перенесённые) дни.
</div>

<table id="tbl">
  <thead>
    <tr>
      <th class="col-toggle"></th>
      <th data-key="trust">Организации по уровню доверия<span class="arrow"></span></th>
      <th data-key="addr">Адрес<span class="arrow"></span></th>
      <th data-key="date">Дата заключения ДУ с УК<span class="arrow"></span></th>
      <th data-key="marker">Результат проверки ЭП<span class="arrow"></span></th>
      <th class="no-sort">Протокол проверки</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div id="overlay">
  <div class="frame">
    <div class="close" onclick="closeOverlay()">×</div>
    <div class="paused-badge">ПАУЗА</div>
    <img id="ov_img0" alt="">
    <img id="ov_img1" alt="">
    <img id="ov_img2" alt="">
    <img id="ov_img3" alt="">
    <div class="caption" id="ov_caption"></div>
  </div>
</div>

<script>
const DATA = __DATA__;
const ORG_STATS_BACKEND = __ORG_STATS__;
const ORG_SUMMARY_BACKEND = __ORG_SUMMARY__;
const MASS_DATE_MIN = __MASS_DATE_MIN__;
const MASS_DATE_HINT = __MASS_DATE_HINT__;

let sortKey = "trust";
let sortAsc = true;
let slideshowTimer = null;
const FRAME_MS = 2000;

let slideshowState = {
  imgs: [],
  cur: 0,
  paused: false,
  activeCount: 0,
};

const expandedOrgs = new Set();

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

function fmtNegDelta(v) {
  if (v === null || v === undefined) return "";
  if (!(v < 0)) return "";
  var s = String(Math.abs(v));
  if (s.indexOf(".") >= 0) {
    s = s.replace(/0+$/, "").replace(/\.$/, "");
  }
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

function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function pastelColorForDate(dateStr) {
  const h = hashCode(dateStr) % 360;
  return "hsl(" + h + ", 50%, 92%)";
}

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

  orgs.forEach(org => {
    const s = ORG_SUMMARY_BACKEND[org] || {
      trust: 0, addr_count: 0, n_count: 0, j_count: 0,
      mass_date: "", mass_count: 0, neg_count: 0, neg_sum: 0,
      holiday_count: 0
    };
    const color = trustColor(s.trust);

    const tr = document.createElement("tr");
    tr.className = "org-row";

    let dateCellHtml = "";
    if (s.mass_date) {
      const bg = pastelColorForDate(s.mass_date);
      dateCellHtml =
        "<td class='mass-date' style='background:" + bg + "'>" +
          esc(s.mass_date) + " <b>(" + s.mass_count + "×)</b>" +
          "<span class='mass-hint'>" + esc(MASS_DATE_HINT) + "</span>" +
        "</td>";
    } else {
      dateCellHtml = "<td></td>";
    }

    /* Н/Д + Н/Д, Н/В */
    const nHtml = "<span class='n-counter'>Н/Д: " + s.n_count + "</span>";
    const jHtml = "<span class='j-counter'>Н/Д, Н/В: " + s.j_count + "</span>";
    const markerCellHtml = "<td>" + nHtml + jHtml + "</td>";

    let addrCellHtml;
    if (s.neg_count > 0) {
      const sumTxt = fmtNegDelta(s.neg_sum);
      addrCellHtml =
        "<td class='addr-cell'>" +
          "<span class='addr-text'>" + s.addr_count + " адр.</span>" +
          "<span class='addr-delta'>" + sumTxt + "</span>" +
        "</td>";
    } else {
      addrCellHtml = "<td>" + s.addr_count + "</td>";
    }

    tr.innerHTML =
      "<td class='toggle-cell'>+</td>" +
      "<td class='org-cell'>" +
        "<span class='org-name'>" + esc(org) + "</span>" +
        "<span class='org-score' style='color:" + color + "'>" +
          s.trust.toFixed(0) + "%" +
        "</span>" +
      "</td>" +
      addrCellHtml +
      dateCellHtml +
      markerCellHtml +
      "<td></td>";

    tbody.appendChild(tr);

    const isOpen = expandedOrgs.has(org);
    const houseRows = [];

    const houseList = byOrg[org].slice();
    houseList.sort((a, b) => {
      let va, vb;
      if (sortKey === "trust") {
        va = (a.date || "").toLowerCase();
        vb = (b.date || "").toLowerCase();
      } else if (sortKey === "marker") {
        va = (a.marker || "").toLowerCase();
        vb = (b.marker || "").toLowerCase();
        if (va === vb) {
          va = (a.date || "").toLowerCase();
          vb = (b.date || "").toLowerCase();
        }
      } else {
        va = (a[sortKey] ?? "").toString().toLowerCase();
        vb = (b[sortKey] ?? "").toString().toLowerCase();
      }
      if (va < vb) return sortAsc ? -1 : 1;
      if (va > vb) return sortAsc ? 1 : -1;
      return 0;
    });

    houseList.forEach(r => {
      const markerCls = r.marker === "Ж" ? "marker-zh"
                       : r.marker === "Н" ? "marker-n"
                       : "marker-other";
      const mass = (s.mass_date && r.date === s.mass_date);

      /* дата: пастельный фон (если массовая) + красный шрифт (если выходной) */
      const dateClasses = [];
      if (mass) dateClasses.push("mass-date");
      if (r.is_holiday) dateClasses.push("holiday-date");
      const dateClass = dateClasses.length
        ? " class='" + dateClasses.join(" ") + "'"
        : "";
      const dateStyle = mass
        ? " style='background:" + pastelColorForDate(r.date) + "'"
        : "";

      let addrCell;
      const dv = (r.delta_value === null || r.delta_value === undefined)
        ? null : r.delta_value;
      const deltaTxt = (dv !== null && dv < 0) ? fmtNegDelta(dv) : "";
      if (deltaTxt) {
        addrCell =
          "<td class='addr-cell house-addr'>" +
            "<span class='addr-text'>" + esc(r.addr) + "</span>" +
            "<span class='addr-delta'>" + deltaTxt + "</span>" +
          "</td>";
      } else {
        addrCell =
          "<td class='house-addr'>" + esc(r.addr) + "</td>";
      }

      const htr = document.createElement("tr");
      htr.className = "house-row" + (isOpen ? " visible" : "");
      htr.innerHTML =
        "<td></td>" +
        "<td></td>" +
        addrCell +
        "<td" + dateClass + dateStyle + ">" + esc(r.date) + "</td>" +
        "<td class='" + markerCls + "'>" + esc(r.marker_long) + "</td>" +
        "<td><span class='btn-view'>Смотреть</span></td>";

      const btn = htr.querySelector(".btn-view");
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openOverlay(r);
      });

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

  document.getElementById("count").textContent = DATA.length;
}

function openOverlay(record) {
  const ov = document.getElementById("overlay");
  const imgs = [
    document.getElementById("ov_img0"),
    document.getElementById("ov_img1"),
    document.getElementById("ov_img2"),
    document.getElementById("ov_img3"),
  ];

  const srcs = [record.scr_a, record.scr_b, record.scr_c];
  if (record.scr_pdf) srcs.push(record.scr_pdf);

  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  imgs.forEach(im => { im.classList.remove("active"); im.src = ""; });

  srcs.forEach((src, i) => {
    imgs[i].src = src;
  });
  imgs[0].classList.add("active");

  slideshowState.imgs = imgs;
  slideshowState.cur = 0;
  slideshowState.paused = false;
  slideshowState.activeCount = srcs.length;

  ov.classList.remove("paused");
  document.getElementById("ov_caption").textContent =
    "Для паузы нажми Пробел или тапни по экрану";
  ov.classList.add("open");

  startSlideshowTimer();
}

function startSlideshowTimer() {
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  const n = slideshowState.activeCount;
  slideshowTimer = setInterval(() => {
    if (slideshowState.paused) return;
    if (slideshowState.cur >= n - 1) {
      clearInterval(slideshowTimer);
      slideshowTimer = null;
      closeOverlay();
      return;
    }
    slideshowState.imgs[slideshowState.cur].classList.remove("active");
    slideshowState.cur += 1;
    slideshowState.imgs[slideshowState.cur].classList.add("active");
  }, FRAME_MS);
}

function togglePause() {
  const ov = document.getElementById("overlay");
  slideshowState.paused = !slideshowState.paused;
  if (slideshowState.paused) ov.classList.add("paused");
  else ov.classList.remove("paused");
}

function closeOverlay() {
  const ov = document.getElementById("overlay");
  ov.classList.remove("open");
  ov.classList.remove("paused");
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  slideshowState.imgs.forEach(im => {
    im.classList.remove("active");
    im.src = "";
  });
  slideshowState.imgs = [];
  slideshowState.cur = 0;
  slideshowState.paused = false;
  slideshowState.activeCount = 0;
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeOverlay();
    return;
  }
  if (e.key === " " || e.code === "Space") {
    const ov = document.getElementById("overlay");
    if (ov.classList.contains("open")) {
      e.preventDefault();
      togglePause();
    }
  }
});

document.getElementById("overlay").addEventListener("click", e => {
  const ov = document.getElementById("overlay");
  if (!ov.classList.contains("open")) return;
  if (e.target.classList.contains("close")) return;
  if (e.target.tagName === "IMG") {
    togglePause();
  } else if (e.target.id === "overlay"
             || e.target.classList.contains("frame")
             || e.target.classList.contains("caption")
             || e.target.classList.contains("paused-badge")) {
    if (e.target.id === "overlay") closeOverlay();
    else togglePause();
  }
});

document.getElementById("overlay").addEventListener("contextmenu", e => {
  return true;
});

document.querySelectorAll("th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) {
      sortAsc = !sortAsc;
    } else {
      sortKey = key;
      sortAsc = true;
    }
    document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
    th.querySelector(".arrow").textContent = sortAsc ? "▲" : "▼";
    render();
  });
});

document.getElementById("mass_min").textContent = MASS_DATE_MIN;

document.getElementById("btn-expand-all").addEventListener("click", () => {
  const btn = document.getElementById("btn-expand-all");
  const allOrgs = new Set(DATA.map(r => r.org));
  const allExpanded = [...allOrgs].every(o => expandedOrgs.has(o));

  if (allExpanded) {
    expandedOrgs.clear();
    btn.textContent = "Развернуть все";
  } else {
    allOrgs.forEach(o => expandedOrgs.add(o));
    btn.textContent = "Свернуть все";
  }
  render();
});

render();
</script>
</body>
</html>
"""

# ============================== ОЧИСТКА JPG ==============================
def trash_unused_jpgs(records):
    """
    Найти все scrNNNN.jpg, использованные в contracts*.log.
    Найти все *.jpg в JPG_DIR, которых там нет.
    Переместить их в TRASH_DIR (D:\\IN\\OLD\\JPG\\).
    """
    used = set()
    for r in records:
        for k in ("scr_a", "scr_b", "scr_c", "scr_pdf"):
            v = r.get(k) or ""
            if v:
                used.add(v)

    all_jpgs = sorted(JPG_DIR.glob("*.jpg"), key=lambda p: p.name)
    unused = [p for p in all_jpgs if p.name not in used]

    print(f"[+] использовано скринов: {len(used)}")
    print(f"[+] всего .jpg в JPG_DIR: {len(all_jpgs)}")
    print(f"[+] неиспользованных: {len(unused)}")
    for p in unused:
        print(f"    — {p.name}")

    if not unused:
        return

    try:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[!] не удалось создать {TRASH_DIR}: {e}")
        return

    moved = 0
    skipped_exists = 0
    skipped_missing = 0
    errors = 0

    for p in unused:
        try:
            if not p.exists():
                skipped_missing += 1
                continue

            target = TRASH_DIR / p.name
            if target.exists():
                skipped_exists += 1
                continue

            shutil.move(str(p), str(target))
            moved += 1
        except Exception as e:
            errors += 1
            print(f"    [!] {p.name}: {e}")

    print(f"[+] перемещено в {TRASH_DIR}: {moved}")
    if skipped_exists:
        print(f"[i] уже было в TRASH_DIR: {skipped_exists}")
    if skipped_missing:
        print(f"[i] не найдено (уже перенесено/удалено): {skipped_missing}")
    if errors:
        print(f"[!] ошибок: {errors}")

# ============================== MAIN ==============================
def main():
    if not LOG_DIR.exists():
        print(f"[!] нет папки {LOG_DIR}")
        return

    records = parse_contracts_logs()
    if not records:
        print(f"[!] нет записей в contracts*.log")
        return

    org_stats = parse_gisjkh_logs()
    print(f"[+] gisjkh-ufo*.log: УК в статистике — {len(org_stats)}")

    mass_per_org = compute_mass_date_per_org(records)
    print(f"[+] УК с массовой датой: {len(mass_per_org)}")
    for org, m in mass_per_org.items():
        print(f"    {org[:50]:50s}  {m['date']} × {m['count']}")

    org_summary = compute_org_summary(records, org_stats, mass_per_org)

    total_neg = sum(1 for r in records
                    if r.get("delta_value") is not None
                    and r["delta_value"] < 0)
    print(f"[+] домов с отрицательной дельтой (Δ<0): {total_neg}")

    total_hol = sum(1 for r in records if r.get("is_holiday"))
    print(f"[+] домов с датой ДУ в выходной/праздник: {total_hol}")

    print(f"[+] сводка по УК (top-10 по trust ↑):")
    for org, s in sorted(org_summary.items(),
                         key=lambda kv: kv[1]["trust"])[:10]:
        print(f"    {org[:50]:50s}  trust={s['trust']:5.1f}%  "
              f"addr={s['addr_count']:3d}  "
              f"Н={s['n_count']:3d}  Ж={s['j_count']:3d}  "
              f"Δ<0: n={s['neg_count']:3d} sum={s['neg_sum']:>10.1f}  "
              f"hol={s['holiday_count']:3d}  "
              f"mass={s['mass_date'] or '-'}")

    data_js = json.dumps(records, ensure_ascii=False)
    org_js = json.dumps(org_stats, ensure_ascii=False)
    summary_js = json.dumps(org_summary, ensure_ascii=False)
    mass_min_js = str(MASS_DATE_MIN)
    mass_hint_js = json.dumps(MASS_DATE_HINT, ensure_ascii=False)

    html_text = (HTML_TEMPLATE
                 .replace("__DATA__", data_js)
                 .replace("__ORG_STATS__", org_js)
                 .replace("__ORG_SUMMARY__", summary_js)
                 .replace("__MASS_DATE_MIN__", mass_min_js)
                 .replace("__MASS_DATE_HINT__", mass_hint_js))

    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(f"[+] report: {OUT_HTML}")
    print(f"[+] records: {len(records)}")

    trash_unused_jpgs(records)

if __name__ == "__main__":
    main()
