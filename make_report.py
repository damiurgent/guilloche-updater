# -*- coding: utf-8 -*-
"""
make_report.py — генератор HTML-отчёта по проверке ЭП договоров УК.

Источники (все читаются "склеенными"):
  1) %TEMP%\\IN\\LOG\\contracts*.log  — таблица нарушителей (Н / Ж)
                                        с 3 или 4 скриншотами на дом.
  2) %TEMP%\\IN\\LOG\\gisjkh-ufo*.log — общий лог проверок, из него берём
                                        знаменатель для расчёта уровня доверия:
                                        сколько домов у каждой УК проверено,
                                        сколько из них Д / Н / Ж.

Уровень доверия по УК = Д / (Д + Н + Ж) × 100%.
    - 100% — все проверенные дома с действительной ЭП;
    -   0% — ни одного Д (все Н или Ж).
Цвет шрифта — по тепловой шкале (голубой → бордовый).

Структура отчёта — дерево:
    строка-УК (свёрнуто)
      + раскрывающиеся строки-дома (по клику на '+')

Свёрнутая строка УК:
    1) Организация + trust (как в развёрнутом)
    2) Адрес — цифрой количество проверенных адресов
    3) Дата ДУ — только если есть массовая дата (одна, максимальная по count)
       + количество повторов + комментарий
       "проверить кворумы МКД по бюллетеням и законность пролонгации"
       Подсвечивается пастельным цветом по самой дате.
    4) Результат ЭП — две цифры: Н (оранжевая), Ж (бордовая), через отступ
    5) Протокол проверки — пусто

Сортировка: по trust ↑ (по возрастанию), tie — по имени УК.
Массовые даты: у одной УК может быть несколько дат с ≥2 повторами.
    В легенду/ячейку идёт ОДНА — с максимальным count.
    Если несколько делят максимум — берётся первая по появлению.

Слайдшоу: 3 или 4 кадра (по наличию 4-й строки в блоке дома).
    - Пауза/продолжение: пробел или тап по экрану.
    - Автозакрытие после последнего кадра.
    - Правый клик / долгое нажатие — системное "Сохранить картинку как…".
    - В подписи вместо имени PDF — "Для паузы нажми Пробел или тапни по экрану".

После генерации: неиспользованные JPG → в корзину (send2trash),
    но строка закомментирована до отладки основного функционала.
"""

import re
import json
from pathlib import Path
from tempfile import gettempdir

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
LOG_DIR = BASE_DIR / "LOG"
JPG_DIR = BASE_DIR / "JPG"
OUT_HTML = JPG_DIR / "report.html"

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


def _iter_contract_files():
    files = sorted(LOG_DIR.glob("contracts*.log"), key=lambda p: p.name)
    return files


def parse_contracts_logs():
    """
    Читает все contracts*.log и склеивает их в один список записей.
    Блок = 3 или 4 непустые строки, разделённые пустой строкой.
    """
    records = []
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
    """
    Читает все gisjkh-ufo*.log и склеивает статистику по УК.
    Возвращает: {org: {"total": N, "Д": n, "Н": n, "Ж": n}}
    """
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
    """
    Для каждой УК:
        - считаем количество каждой даты ДУ,
        - оставляем только даты с count >= MASS_DATE_MIN,
        - из них выбираем ОДНУ с максимальным count,
          tie → первая по появлению в records.
    Возвращает: {org: {"date": ..., "count": N}}
    """
    counts_per_org = {}   # org -> {date: count}
    order_per_org = {}    # org -> [dates в порядке появления]

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
        # первая по появлению среди дат с максимальным count
        for d in order_per_org[org]:
            if counts.get(d) == max_count and counts[d] >= MASS_DATE_MIN:
                result[org] = {"date": d, "count": max_count}
                break
    return result


# ============================== СВОДКА ПО УК ==============================
def compute_org_summary(records, org_stats, mass_per_org):
    """
    Для каждой УК — сводка для свёрнутой строки:
      trust, addr_count, n_count, j_count, mass_date, mass_count
    """
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
            }
        s = summary[org]
        s["addr_count"] += 1
        if r["marker"] == "Н":
            s["n_count"] += 1
        elif r["marker"] == "Ж":
            s["j_count"] += 1

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

  /* строка-УК */
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

  td.marker-zh  { color: #b00; font-weight: bold; }
  td.marker-n   { color: #d68000; font-weight: bold; }
  td.marker-other { color: #333; font-weight: bold; }

  /* свёрнутая ячейка Н/Ж: две цифры */
  .n-counter { color: #d68000; font-weight: bold; }
  .j-counter { color: #b00; font-weight: bold; margin-left: 14px; }

  /* строка-дом (скрытая по умолчанию) */
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
  <b>Уровень доверия</b> по УК = Д / (Д + Н + Ж) × 100%,
  где Д — дома с действительной ЭП. Цвет — по тепловой шкале
  (голубой = высокий, бордовый = низкий).
  Сортировка по умолчанию — по уровню доверия <b>↑</b> (аутсайдеры сверху).
  <br>
  <b>Массовые даты</b> (у одной УК, ≥<span id="mass_min">2</span> раз)
  показаны в свёрнутой строке УК — берётся дата с максимальным
  числом повторов. Подсвечены пастельным цветом.
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

/* --- глобальное состояние слайдшоу --- */
let slideshowState = {
  imgs: [],
  cur: 0,
  paused: false,
  activeCount: 0,
};

/* какие УК сейчас раскрыты (чтобы не схлопывались при сортировке) */
const expandedOrgs = new Set();

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

/* ---------- уровень доверия: цвет по шкале ---------- */
function trustColor(trust) {
  if (trust >= 99.5) return "hsl(200, 70%, 50%)"; // голубой
  if (trust >= 81)   return "hsl(140, 60%, 40%)"; // зелёный
  if (trust >= 61)   return "hsl(80, 65%, 40%)";  // жёлто-зелёный
  if (trust >= 41)   return "hsl(50, 80%, 40%)";  // жёлтый
  if (trust >= 21)   return "hsl(30, 85%, 45%)";  // оранжевый
  if (trust >= 0.5)  return "hsl(10, 80%, 45%)";  // красный
  return "hsl(350, 70%, 35%)";                    // бордовый
}

/* ---------- пастельный цвет по строке даты ---------- */
function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function pastelColorForDate(dateStr) {
  const h = hashCode(dateStr) % 360;
  return "hsl(" + h + ", 50%, 92%)";
}

/* ---------- рендер ---------- */
function render() {
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";

  /* группируем дома по УК */
  const byOrg = {};
  DATA.forEach(r => {
    if (!byOrg[r.org]) byOrg[r.org] = [];
    byOrg[r.org].push(r);
  });

  const orgs = Object.keys(byOrg);

  /* сортировка по trust ↑, tie — по имени УК */
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
      mass_date: "", mass_count: 0
    };
    const color = trustColor(s.trust);

    /* --- строка УК --- */
    const tr = document.createElement("tr");
    tr.className = "org-row";

    /* ячейка 3: массовая дата */
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

    /* ячейка 4: Н (оранж) + Ж (бордо) */
    const nHtml = "<span class='n-counter'>Н: " + s.n_count + "</span>";
    const jHtml = "<span class='j-counter'>Ж: " + s.j_count + "</span>";
    const markerCellHtml = "<td>" + nHtml + jHtml + "</td>";

    tr.innerHTML =
      "<td class='toggle-cell'>+</td>" +
      "<td class='org-cell'>" +
        "<span class='org-name'>" + esc(org) + "</span>" +
        "<span class='org-score' style='color:" + color + "'>" +
          s.trust.toFixed(0) + "%" +
        "</span>" +
      "</td>" +
      "<td>" + s.addr_count + "</td>" +
      dateCellHtml +
      markerCellHtml +
      "<td></td>";

    tbody.appendChild(tr);

    /* --- строки домов (скрытые или уже раскрытые) --- */
    const isOpen = expandedOrgs.has(org);
    const houseRows = [];

    /* сортируем дома внутри УК по тому же ключу, что и УК */
    const houseList = byOrg[org].slice();
    houseList.sort((a, b) => {
      let va, vb;
      if (sortKey === "trust") {
        // trust относится к УК, не к дому — вторичный ключ дата
        va = (a.date || "").toLowerCase();
        vb = (b.date || "").toLowerCase();
      } else if (sortKey === "marker") {
        // сортируем по самому маркеру (Н / Ж / Д), затем по дате
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
      const dateStyle = mass
        ? " style='background:" + pastelColorForDate(r.date) + "'"
        : "";
      const dateClass = mass ? " class='mass-date'" : "";

      const htr = document.createElement("tr");
      htr.className = "house-row" + (isOpen ? " visible" : "");
      htr.innerHTML =
        "<td></td>" +
        "<td></td>" +
        "<td class='house-addr'>" + esc(r.addr) + "</td>" +
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

    /* --- toggle --- */
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

/* ---------- слайдшоу ---------- */
function openOverlay(record) {
  const ov = document.getElementById("overlay");
  const imgs = [
    document.getElementById("ov_img0"),
    document.getElementById("ov_img1"),
    document.getElementById("ov_img2"),
    document.getElementById("ov_img3"),
  ];

  /* собираем список доступных скринов:
     3 базовых + опционально 4-й (scr_pdf) */
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

/* пробел → пауза/продолжить */
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

/* клик вне кадра → закрыть; клик по кадру → пауза/продолжить */
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

/* --- контекстное меню: не мешаем браузеру "Сохранить картинку как…" --- */
document.getElementById("overlay").addEventListener("contextmenu", e => {
  /* никаких preventDefault: даём системное меню */
  return true;
});

/* сортировка по клику на заголовок */
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

/* --- кнопка «Развернуть/Свернуть все» --- */
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


# ============================== КОРЗИНА ==============================
def trash_unused_jpgs(records):
    """
    Найти все scrNNNN.jpg, использованные в contracts*.log.
    Найти все *.jpg в JPG_DIR, которых там нет.
    Переместить их в корзину (send2trash).
    СТРОКА С send2trash ЗАКОММЕНТИРОВАНА до полной отладки.
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

    # --- раскомментировать после отладки ---
    # try:
    #     from send2trash import send2trash
    #     for p in unused:
    #         send2trash(str(p))
    #     print(f"[+] перемещено в корзину: {len(unused)}")
    # except ImportError:
    #     print("[!] send2trash не установлен — пропуск")
    # except Exception as e:
    #     print(f"[!] ошибка корзины: {e}")

    print("[i] корзина отключена (строка закомментирована) — пропуск")


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

    # --- отладочный вывод топа по trust ---
    print(f"[+] сводка по УК (top-10 по trust ↑):")
    for org, s in sorted(org_summary.items(),
                         key=lambda kv: kv[1]["trust"])[:10]:
        print(f"    {org[:50]:50s}  trust={s['trust']:5.1f}%  "
              f"addr={s['addr_count']:3d}  "
              f"Н={s['n_count']:3d}  Ж={s['j_count']:3d}  "
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
