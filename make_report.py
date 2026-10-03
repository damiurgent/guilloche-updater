# -*- coding: utf-8 -*-
"""
make_report.py — генератор HTML-отчёта.

Источники:
  1) %TEMP%\\IN\\LOG\\contracts.log   — таблица нарушителей (Н / Ж) с 3 скриншотами.
  2) %TEMP%\\IN\\LOG\\gisjkh-ufo.log  — общий лог проверок, из него берём
                                        знаменатель для расчёта уровня доверия:
                                        сколько домов у каждой УК проверено,
                                        сколько из них Д / Н / Ж.

Уровень доверия по УК = Д / (Д + Н + Ж) × 100%.
    - 100% — все проверенные дома с действительной ЭП;
    -   0% — ни одного Д (все Н или Ж).
Цвет шрифта — по тепловой шкале (голубой → бордовый).

Столбцы отчёта:
    Организации по уровню доверия | Адрес | Дата заключения ДУ с УК |
    Результат проверки ЭП | Протокол проверки (кнопка).

Массовые даты (у одной УК, ≥2 раз) подсвечиваются пастельным фоном.
Слайдшоу — 3 картинки, 2 сек, авто-закрытие после третьего кадра.
"""

import re
import json
from pathlib import Path
from tempfile import gettempdir

BASE_DIR = Path(gettempdir()) / "IN"
LOG_DIR = BASE_DIR / "LOG"
JPG_DIR = BASE_DIR / "JPG"
CONTRACT_LOG = LOG_DIR / "contracts.log"
GISJKH_LOG = LOG_DIR / "gisjkh-ufo.log"
OUT_HTML = JPG_DIR / "report.html"


MARKER_LONG = {
    "Н": "Подпись верна, но НЕДЕЙСТВИТЕЛЬНА",
    "Ж": "Подпись НЕВЕРНА и НЕДЕЙСТВИТЕЛЬНА",
    "Д": "Подпись ДЕЙСТВИТЕЛЬНА",
}

# Порог массовости даты в пределах одной УК
MASS_DATE_MIN = 2


# ---------------------------------------------------------------------------
# Чтение contracts.log (таблица нарушителей)
# ---------------------------------------------------------------------------
def parse_contracts_log(path: Path):
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    records = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) != 3:
            continue

        m1 = re.match(
            r"^(scr\d{4}\.jpg)\s*-\s*(.+?)\s*--\s*(.+?)\s*---\s*(.+?)\s*----\s*(.+)$",
            lines[0]
        )
        if not m1:
            continue
        scr_a, org, addr, pdf_name, date_str = m1.groups()

        m2 = re.match(
            r"^(scr\d{4}\.jpg)\s*-\s*(.+?)\s*--\s*(.+)$",
            lines[1]
        )
        if not m2:
            continue
        scr_b, _pdf_again, ep_name = m2.groups()

        m3 = re.match(r"^(scr\d{4}\.jpg)\s*-\s*(.+)$", lines[2])
        if not m3:
            continue
        scr_c, marker = m3.groups()

        records.append({
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
        })
    return records


# ---------------------------------------------------------------------------
# Чтение gisjkh-ufo.log (знаменатель для уровня доверия)
# ---------------------------------------------------------------------------
# Ожидаемый формат строк:
#   house_info: org='ООО "УК" ...' addr='...' pdf='...' date='...'
#   result_text: 'НЕДЕЙСТВИТЕЛЬНА'  marker: 'Н'
#   result_text: 'ДЕЙСТВИТЕЛЬНА'  marker: None
# ---------------------------------------------------------------------------

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


def parse_gisjkh_log(path: Path):
    """
    Читает gisjkh-ufo.log, возвращает dict:
        {org: {"total": N, "Д": n, "Н": n, "Ж": n}}
    """
    stats = {}
    if not path.exists():
        return stats

    current_org = None

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m_hi = RE_HOUSE_INFO.search(line)
            if m_hi:
                current_org = m_hi.group(1).strip()
                continue

            # результат с маркером ('Н' или 'Ж')
            m_res = RE_RESULT_MARKER.search(line)
            if m_res and current_org:
                _txt, marker = m_res.group(1).strip(), m_res.group(2).strip()
                if marker in ("Н", "Ж"):
                    _bump(stats, current_org, marker)
                elif marker == "":
                    # неожиданный пустой маркер — пропускаем
                    pass
                current_org = None
                continue

            # действительная подпись (marker: None)
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


# ---------------------------------------------------------------------------
# HTML-шаблон
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Отчёт по проверке ЭП договоров УК</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         margin: 20px; background: #f5f5f5; color: #222; }
  h1 { font-size: 20px; margin-bottom: 10px; }
  .meta { color: #666; font-size: 13px; margin-bottom: 16px; }
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
  tr:nth-child(even) td { background: #fafafa; }
  tr:nth-child(even) td.mass-date { background: inherit; }

  /* ячейка организации: две строки, разные выключки */
  td.org-cell { white-space: normal; }
  td.org-cell .org-name {
      display: block; text-align: left; font-weight: 600;
  }
  td.org-cell .org-score {
      display: block; text-align: right; font-weight: 700;
      font-variant-numeric: tabular-nums;
  }

  td.mass-date { font-weight: 600; }

  .marker-zh { color: #b00; font-weight: bold; }
  .marker-n  { color: #d68000; font-weight: bold; }
  .marker-other { color: #333; font-weight: bold; }

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
    color: #ddd; font-size: 12px; font-family: monospace;
    background: rgba(0,0,0,0.5); padding: 4px 8px; border-radius: 4px;
  }
</style>
</head>
<body>
<h1>Отчёт по проверке квалифицированной ЭП договоров УК</h1>
<div class="meta">
  Записей: <span id="count">0</span>.
  Источник: contracts.log (таблица), gisjkh-ufo.log (знаменатель).
  Сортировка: щёлкните по заголовку столбца.
  Просмотр протокола: кнопка «Смотреть».
  <br>
  <b>Уровень доверия</b> по УК = Д / (Д + Н + Ж) × 100%,
  где Д — дома с действительной ЭП. Цвет шрифта по тепловой шкале
  (голубой = высокий, бордовый = низкий).
  <b>Массовые даты</b> (у одной УК, ≥2 раз) подсвечены пастельными цветами.
  Легенда массовых дат: <span id="legend"></span>
</div>

<table id="tbl">
  <thead>
    <tr>
      <th data-key="trust">Организации по уровню доверия<span class="arrow"></span></th>
      <th data-key="addr">Адрес<span class="arrow"></span></th>
      <th data-key="date">Дата заключения ДУ с УК<span class="arrow"></span></th>
      <th data-key="marker_long">Результат проверки ЭП<span class="arrow"></span></th>
      <th class="no-sort">Протокол проверки</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div id="overlay">
  <div class="frame">
    <div class="close" onclick="closeOverlay()">×</div>
    <img id="ov_img0" alt="">
    <img id="ov_img1" alt="">
    <img id="ov_img2" alt="">
    <div class="caption" id="ov_caption"></div>
  </div>
</div>

<script>
const DATA = __DATA__;
const ORG_STATS_BACKEND = __ORG_STATS__;

let sortKey = "trust";
let sortAsc = true;
let slideshowTimer = null;
const FRAME_MS = 2000;

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

/* ---------- уровень доверия ---------- *
 * ORG_STATS_BACKEND приходит из Python-парсера gisjkh-ufo.log.
 * Формат: { "ООО ...": {"total": N, "Д": d, "Н": n, "Ж": j}, ... }
 * Если УК нет в этом словаре — доверие 0%.
 */
const ORG_STATS = {};
DATA.forEach(r => {
  if (!ORG_STATS[r.org]) {
    const backend = ORG_STATS_BACKEND[r.org] || null;
    if (backend && backend.total > 0) {
      const trust = (backend["Д"] || 0) / backend.total * 100;
      ORG_STATS[r.org] = { trust: trust, total: backend.total, d: backend["Д"] || 0 };
    } else {
      ORG_STATS[r.org] = { trust: 0, total: 0, d: 0 };
    }
  }
});

function trustColor(trust) {
  if (trust >= 99.5) return "hsl(200, 70%, 50%)"; // голубой
  if (trust >= 81)   return "hsl(140, 60%, 40%)"; // зелёный
  if (trust >= 61)   return "hsl(80, 65%, 40%)";  // жёлто-зелёный
  if (trust >= 41)   return "hsl(50, 80%, 40%)";  // жёлтый
  if (trust >= 21)   return "hsl(30, 85%, 45%)";  // оранжевый
  if (trust >= 0.5)  return "hsl(10, 80%, 45%)";  // красный
  return "hsl(350, 70%, 35%)";                    // бордовый
}

/* ---------- массовые даты в пределах одной УК ---------- */
const ORG_DATE_COUNTS = {};
DATA.forEach(r => {
  const key = r.org + "||" + r.date;
  ORG_DATE_COUNTS[key] = (ORG_DATE_COUNTS[key] || 0) + 1;
});

function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function pastelColorForDate(dateStr) {
  const h = hashCode(dateStr) % 360;
  return "hsl(" + h + ", 50%, 92%)";
}

function isMassDate(org, date) {
  return (ORG_DATE_COUNTS[org + "||" + date] || 0) >= 2;
}

function buildLegend() {
  const el = document.getElementById("legend");
  if (!el) return;
  const entries = [];
  Object.keys(ORG_DATE_COUNTS).forEach(key => {
    if (ORG_DATE_COUNTS[key] >= 2) {
      const parts = key.split("||");
      entries.push({ org: parts[0], date: parts[1], n: ORG_DATE_COUNTS[key] });
    }
  });
  if (entries.length === 0) {
    el.textContent = "массовых дат не обнаружено";
    return;
  }
  entries.sort((a, b) => b.n - a.n);
  el.innerHTML = entries.map(e => {
    const color = pastelColorForDate(e.date);
    return "<span style='display:inline-block; padding:1px 6px; " +
           "margin:0 4px; border:1px solid #ccc; border-radius:3px; " +
           "background:" + color + "'>" + esc(e.date) +
           " (" + e.n + ")</span>";
  }).join(" ");
}

/* ---------- рендер ---------- */
function render() {
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";

  const rows = DATA.slice();
  rows.sort((a, b) => {
    let va, vb;
    if (sortKey === "trust") {
      va = ORG_STATS[a.org].trust;
      vb = ORG_STATS[b.org].trust;
    } else {
      va = (a[sortKey] ?? "").toString().toLowerCase();
      vb = (b[sortKey] ?? "").toString().toLowerCase();
    }
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return 0;
  });

  rows.forEach(r => {
    const stats = ORG_STATS[r.org];
    const color = trustColor(stats.trust);

    const markerCls = r.marker === "Ж" ? "marker-zh"
                     : r.marker === "Н" ? "marker-n"
                     : "marker-other";

    const mass = isMassDate(r.org, r.date);
    const dateClass = mass ? " class='mass-date'" : "";
    const dateStyle = mass
      ? " style='background:" + pastelColorForDate(r.date) + "'"
      : "";

    const tr = document.createElement("tr");

    tr.innerHTML =
      "<td class='org-cell'>" +
        "<span class='org-name'>" + esc(r.org) + "</span>" +
        "<span class='org-score' style='color:" + color + "'>" +
          stats.trust.toFixed(0) + "%" +
        "</span>" +
      "</td>" +
      "<td>" + esc(r.addr) + "</td>" +
      "<td" + dateClass + dateStyle + ">" + esc(r.date) + "</td>" +
      "<td class='" + markerCls + "'>" + esc(r.marker_long) + "</td>" +
      "<td><span class='btn-view'>Смотреть</span></td>";

    const btn = tr.querySelector(".btn-view");
    btn.addEventListener("click", () => {
      openOverlay(r.scr_a, r.scr_b, r.scr_c, r.pdf_name + " | " + r.date);
    });

    tbody.appendChild(tr);
  });

  document.getElementById("count").textContent = DATA.length;
  buildLegend();
}

/* ---------- слайдшоу ---------- */
function openOverlay(a, b, c, caption) {
  const ov = document.getElementById("overlay");
  const imgs = [
    document.getElementById("ov_img0"),
    document.getElementById("ov_img1"),
    document.getElementById("ov_img2"),
  ];

  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  imgs.forEach(im => { im.classList.remove("active"); im.src = ""; });

  imgs[0].src = a;
  imgs[1].src = b;
  imgs[2].src = c;
  imgs[0].classList.add("active");

  document.getElementById("ov_caption").textContent = caption;
  ov.classList.add("open");

  let cur = 0;
  slideshowTimer = setInterval(() => {
    if (cur >= imgs.length - 1) {
      clearInterval(slideshowTimer);
      slideshowTimer = null;
      closeOverlay();
      return;
    }
    imgs[cur].classList.remove("active");
    cur += 1;
    imgs[cur].classList.add("active");
  }, FRAME_MS);
}

function closeOverlay() {
  const ov = document.getElementById("overlay");
  ov.classList.remove("open");
  if (slideshowTimer) { clearInterval(slideshowTimer); slideshowTimer = null; }
  ["ov_img0","ov_img1","ov_img2"].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove("active");
    el.src = "";
  });
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeOverlay();
});

document.getElementById("overlay").addEventListener("click", e => {
  if (e.target.id === "overlay" || e.target.classList.contains("frame")) {
    closeOverlay();
  }
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

render();
</script>
</body>
</html>
"""


def main():
    records = parse_contracts_log(CONTRACT_LOG)
    if not records:
        print(f"[!] нет записей в {CONTRACT_LOG}")
        return

    org_stats = parse_gisjkh_log(GISJKH_LOG)
    print(f"[+] gisjkh-ufo.log: УК в статистике — {len(org_stats)}")
    for org, s in sorted(org_stats.items(), key=lambda kv: kv[1]["total"], reverse=True)[:10]:
        d = s.get("Д", 0); n = s.get("Н", 0); j = s.get("Ж", 0)
        trust = d / s["total"] * 100 if s["total"] else 0
        print(f"    {org[:50]:50s}  total={s['total']:3d}  Д={d:3d}  Н={n:3d}  Ж={j:3d}  trust={trust:5.1f}%")

    data_js = json.dumps(records, ensure_ascii=False)
    org_js = json.dumps(org_stats, ensure_ascii=False)
    html_text = (HTML_TEMPLATE
                 .replace("__DATA__", data_js)
                 .replace("__ORG_STATS__", org_js))
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(f"[+] report: {OUT_HTML}")
    print(f"[+] records: {len(records)}")


if __name__ == "__main__":
    main()