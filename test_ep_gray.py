# -*- coding: utf-8 -*-
"""
scan_ep_dom.py — v20.

Правки относительно v19:
  - Формат OUT_FILE приведён к виду базового скрипта:
      scrXXXX.jpg - <org> -- <addr> --- <pdf_label> ---- <date_label>
      scrXXXX.jpg - (<Pз>)-<Pж>=<Δ> -- <synopsis>
      scrXXXX.jpg - <marker>
      scrXXXX.jpg
    + пустая строка. «scrXXXX.jpg» — литерал (не инкрементируется).
  - build_delta_line возвращает (total_str, res_str, delta_str).
  - OGRN в OUT_FILE не пишется.
  - Литералы для NO_MGMT_PAGE: «Договор не размещен» / «Дата отсутствует».
"""

import os
import re
import time
import traceback
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import gettempdir

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ============================== ПУТИ ==============================
BASE_DIR = Path(gettempdir()) / "IN"
LOG_DIR = BASE_DIR / "LOG"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "scan_ep_dom.log"
OUT_FILE = LOG_DIR / "no_ep_or_no_mgmt.txt"

GIS_URL = "https://my.dom.gosuslugi.ru/#!/houses"

# ============================== РЕЖИМ ==============================
TARGET_OGRN = "1085514000758"

# Литерал вместо номера скрина: у нас скриншотов нет,
# поэтому реальный номер не подставляется.
SCR_STUB = "scrXXXX.jpg"

# ============================== РЕГЭКСПЫ ==============================
CONTRACT_RE = re.compile(
    r"договор|(?<![а-яё])дог(?![а-яё])|dog|(?<![а-яё])ду(?![а-яё])",
    re.IGNORECASE,
)
ADDENDUM_RE = re.compile(r"к\s*ду|к\s*договор", re.IGNORECASE)
PDF_MARK_RE = re.compile(r"\.pdf", re.IGNORECASE)
ADDR_RE = re.compile(r"\d{6},")
MAP_TAIL_RE = re.compile(r"\s*На\s+карте\s*$", re.IGNORECASE)
SELECT2_JL_RE = re.compile(r"^ЮЛ:\s*")
SELECT2_OGRN_TAIL_RE = re.compile(r"\s*\(ОГРН.*$")
NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
TOTAL_RECORDS_RE = re.compile(r"Всего\s+записей[:\s]*(\d+)", re.IGNORECASE)
CONTRACT_DATE_RE = re.compile(
    r"Дата\s+заключения\s+договора\s+управления[^\d]{0,80}(\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)


# ============================== ЛОГ ==============================
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_finding(org: str, addr: str,
                  pdf_label: str, date_label: str,
                  p_total: str, p_res: str, delta_str: str,
                  synopsis: str, marker: str):
    """
    Пишет один блок из 4 строк + пустую в OUT_FILE.
    Пример:
        scrXXXX.jpg - ООО УК "НАШ ДОМ" -- <addr> --- <pdf> ---- <date>
        scrXXXX.jpg - (4058.5)-12507.5=-8449 -- ЭП к договору отсутствует
        scrXXXX.jpg - Э
        scrXXXX.jpg
    """
    if p_total and p_res and delta_str:
        delta_line = f"({p_total})-{p_res}={delta_str}"
    else:
        delta_line = "(0)-0=0"

    line1 = f"{SCR_STUB} - {org} -- {addr} --- {pdf_label} ---- {date_label}"
    line2 = f"{SCR_STUB} - {delta_line} -- {synopsis}"
    line3 = f"{SCR_STUB} - {marker}"
    line4 = f"{SCR_STUB}"

    with open(OUT_FILE, "a", encoding="utf-8") as fh:
        fh.write(line1 + "\n")
        fh.write(line2 + "\n")
        fh.write(line3 + "\n")
        fh.write(line4 + "\n")
        fh.write("\n")

    log(f"    [finding] {addr} | {synopsis} | {delta_line} | {marker}")


# ============================== ДРАЙВЕР ==============================
def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    return driver


# ============================== ЭП ==============================
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


def is_pdf(name: str) -> bool:
    return bool(name) and bool(PDF_MARK_RE.search(name))


def is_target_contract(name: str) -> bool:
    if not name:
        return False
    if not CONTRACT_RE.search(name):
        return False
    if ADDENDUM_RE.search(name):
        return False
    return True


# ============================== НОРМАЛИЗАЦИЯ ОРГ ==============================
def normalize_org(raw: str) -> str:
    """Сокращает организационно-правовые формы в названии организации."""
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


# ============================== ЧИСТКА ==============================
def clean_addr(txt: str) -> str:
    txt = (txt or "").strip()
    txt = MAP_TAIL_RE.sub("", txt).strip()
    txt = txt.rstrip(" ,;.")
    return txt


def clean_org_from_select2(txt: str) -> str:
    t = (txt or "").strip()
    t = SELECT2_JL_RE.sub("", t)
    t = SELECT2_OGRN_TAIL_RE.sub("", t)
    return t.strip()


# ============================== ПЛОЩАДИ ==============================
def read_square_cell(driver, ng_bind_fragment: str) -> str:
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            f"td[ng-bind-html*='{ng_bind_fragment}']"
        )
        if not els:
            return ""
        return (els[0].text or "").strip()
    except Exception:
        return ""


def parse_decimal(raw: str):
    if not raw:
        return None
    m = NUM_RE.search(raw)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _fmt_decimal(d: Decimal) -> str:
    """Форматирует Decimal без научной нотации и без хвостовых нулей."""
    s = f"{d.normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-", "-0"):
        return "0"
    return s


def build_delta_line(driver):
    """
    Возвращает кортеж (total_str, res_str, delta_str):
      total_str, res_str — «сырые» числа из ячеек (4058.5, 12507.5);
      delta_str — строка вида '-8449' или '' при недостатке данных.
    """
    total_raw = read_square_cell(driver, "totalSquare")
    res_raw = read_square_cell(driver, "residentialSquare")
    log(f"      squares raw: total={total_raw!r} residential={res_raw!r}")

    total = parse_decimal(total_raw)
    res = parse_decimal(res_raw)
    if total is None or res is None:
        log("      squares: недостаточно данных — дельта не считается")
        return "", "", ""

    delta = total - res
    log(f"      squares: total={total} residential={res} delta={delta}")
    return _fmt_decimal(total), _fmt_decimal(res), _fmt_decimal(delta)


# ============================== НАВИГАЦИЯ ==============================
def open_uk_tab(driver, wait):
    time.sleep(3)
    tab = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(., 'Поиск дома по управляющей организации')]")
    ))
    tab.click()
    time.sleep(2)


def drop_select2_mask(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll('.select2-drop-mask').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop-active').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop').forEach(e => e.remove());
        """)
    except Exception:
        pass


def read_input_value(driver) -> str:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "input.select2-input")
        if els:
            return els[0].get_attribute("value") or ""
    except Exception:
        pass
    return ""


def read_selected_text(driver) -> str:
    for sel in ("span.select2-chosen",
                ".select2-selection__rendered",
                "span.select2-selection__rendered"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = (el.text or "").strip()
                if txt:
                    return txt
        except Exception:
            pass
    try:
        txt = driver.execute_script("""
            var el = document.querySelector('span.select2-chosen')
                   || document.querySelector('.select2-selection__rendered');
            return el ? (el.textContent || '').trim() : '';
        """)
        if txt:
            return txt
    except Exception:
        pass
    return ""


def select_org(driver, wait, ogrn: str) -> str:
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
    log(f"    typed: {read_input_value(driver)!r}")

    try:
        result_link = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR,
             "ul.select2-results li.select2-result-selectable a, "
             "ul.select2-results li.select2-result-selectable")
        ))
    except TimeoutException:
        log(f"    OGRN {ogrn} NOT FOUND")
        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass
        return ""

    result_link.click()
    time.sleep(2)

    chosen = read_selected_text(driver)
    log(f"    chosen: {chosen!r}")

    find_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[normalize-space(.)='Найти']")
    ))
    find_btn.click()
    time.sleep(2)

    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located(
            (By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]")
        ))
    except TimeoutException:
        log(f"    no house cards for {ogrn}")
        return ""

    return chosen


def _links_count(driver) -> int:
    try:
        return len(driver.find_elements(
            By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]"
        ))
    except Exception:
        return -1


def set_page_size_100(driver, wait) -> bool:
    """
    Переключаем select#count на 100. На Angular-обвязке Selenium'овский
    Select не триггерит change, поэтому после select_by_value('100')
    дополнительно выстреливаем change-событие через dispatchEvent.
    """
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "select#count")
    except Exception:
        candidates = []

    if not candidates:
        log("    select#count отсутствует — пагинация не нужна")
        return True

    select_el = candidates[0]

    before = _links_count(driver)
    log(f"    link count before page size change: {before}")

    for attempt in range(1, 4):
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", select_el
            )
            time.sleep(0.5)

            try:
                Select(select_el).select_by_value("100")
            except Exception as e:
                log(f"    Select.select_by_value failed: {e}")

            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                select_el
            )

            driver.execute_script("""
                var sel = arguments[0];
                if (sel.value !== '100') {
                    for (var i = 0; i < sel.options.length; i++) {
                        if (sel.options[i].value === '100' ||
                            sel.options[i].textContent.trim() === '100') {
                            sel.value = sel.options[i].value;
                            break;
                        }
                    }
                }
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            """, select_el)

            time.sleep(2)

            after = _links_count(driver)
            log(f"    page size attempt {attempt}: links {before} → {after}")

            if after > before:
                log(f"    page size = 100 установлен (attempt {attempt})")
                return True
        except Exception as e:
            log(f"    set_page_size_100 attempt {attempt} failed: {e}")
            time.sleep(1.5)

    log("    set_page_size_100: НЕ УДАЛОСЬ, продолжаю как есть")
    return False


def read_total_records(driver) -> int:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = TOTAL_RECORDS_RE.search(body)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return -1


def open_new_tab_and_switch(driver, wait, click_fn):
    handles_before = set(driver.window_handles)
    click_fn()
    end = time.time() + 20
    new_handle = None
    while time.time() < end:
        diff = set(driver.window_handles) - handles_before
        if diff:
            new_handle = diff.pop()
            break
        time.sleep(0.3)
    if new_handle is None:
        raise TimeoutException("new tab did not open within 20s")
    driver.switch_to.window(new_handle)
    time.sleep(2)
    return new_handle


def close_tabs_except(driver, keep_handles):
    for h in list(driver.window_handles):
        if h not in keep_handles:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass


# ============================== ОЖИДАНИЯ ==============================
def wait_for_page_fields(driver, timeout: int = 10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            h1_els = driver.find_elements(By.CSS_SELECTOR, "h1.ng-binding")
            for el in h1_els:
                txt = (el.text or "").strip()
                if ADDR_RE.search(txt):
                    return True
            if driver.find_elements(
                By.CSS_SELECTOR,
                "span.form-base__form-value.ng-binding[ng-bind='addressInfo.formattedAddress']"
            ):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def wait_ready_state(driver, timeout: int = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            state = driver.execute_script("return document.readyState;")
            if state == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def wait_for_ep_rendered(driver, timeout: int = 20):
    start = time.time()
    end = start + timeout
    while time.time() < end:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, "span.gis-icon-signature")
            total = len(els)
            if total > 0:
                classified = 0
                for el in els:
                    cls = (el.get_attribute("class") or "").lower()
                    if ("app-icon_cl_prime" in cls) or ("app-icon_cl_asphalt" in cls):
                        classified += 1
                if classified == total:
                    waited = time.time() - start
                    time.sleep(2.0)
                    return True, total, classified, round(waited, 2)
        except Exception:
            pass
        time.sleep(0.3)
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "span.gis-icon-signature")
        total = len(els)
        classified = 0
        for el in els:
            cls = (el.get_attribute("class") or "").lower()
            if ("app-icon_cl_prime" in cls) or ("app-icon_cl_asphalt" in cls):
                classified += 1
    except Exception:
        total, classified = 0, 0
    return False, total, classified, round(time.time() - start, 2)


# ============================== ЧТЕНИЕ ПОЛЕЙ ==============================
def read_page_addr(driver) -> str:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "h1.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if ADDR_RE.search(txt):
                return clean_addr(txt)
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            "span.form-base__form-value.ng-binding[ng-bind='addressInfo.formattedAddress']"
        )
        if els:
            txt = (els[0].text or "").strip()
            if txt:
                return clean_addr(txt)
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, "span.form-base__form-value.ng-binding"
        )
        for el in els:
            txt = (el.text or "").strip()
            if ADDR_RE.search(txt):
                return clean_addr(txt)
    except Exception:
        pass
    return ""


def read_page_org(driver) -> str:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "a.ctrl-link.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if txt and len(txt) > 5:
                return txt
    except Exception:
        pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "a.cnt-link.ng-binding")
        for el in els:
            txt = (el.text or "").strip()
            if txt and len(txt) > 5:
                return txt
    except Exception:
        pass
    return ""


def read_contract_date(driver) -> str:
    """Ищет дату заключения договора управления на текущей странице."""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = CONTRACT_DATE_RE.search(body)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, "span.form-base__form-value.ng-binding"
        )
        for el in els:
            txt = (el.text or "").strip()
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", txt)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


# ============================== СКАНИРОВАНИЕ ФАЙЛОВ ==============================
def scan_files(driver) -> list:
    rows = []
    containers = driver.find_elements(
        By.CSS_SELECTOR,
        "div.file-panel__row.file-panel__justify-start"
    )
    log(f"      file-panel__row: {len(containers)}")

    for idx, row in enumerate(containers):
        try:
            if not row.find_elements(By.CSS_SELECTOR, "span.icon-file_pdf"):
                log(f"        row #{idx}: пропуск (нет icon-file_pdf)")
                continue
        except Exception:
            log(f"        row #{idx}: exception on pdf_icon lookup")
            continue

        name = ""
        try:
            cands = row.find_elements(
                By.CSS_SELECTOR,
                ".file-panel__row-item.file-panel__name span.ng-binding"
            )
            for el in cands:
                try:
                    style = (el.get_attribute("style") or "").lower()
                    text = (el.text or "").strip()
                except Exception:
                    continue
                if "0075c0" in style and text:
                    name = text
                    break
            if not name:
                for el in cands:
                    try:
                        text = (el.text or "").strip()
                    except Exception:
                        text = ""
                    if text and text != "Документ PDF":
                        name = text
                        break
        except Exception as e:
            log(f"        row #{idx}: name lookup fail: {e}")

        ep_cls, ep_color = "", ""
        try:
            sig_els = row.find_elements(By.CSS_SELECTOR, "span.gis-icon-signature")
            if sig_els:
                sig = sig_els[0]
                ep_cls = sig.get_attribute("class") or ""
                try:
                    ep_color = driver.execute_script(
                        "return getComputedStyle(arguments[0]).color;", sig
                    ) or ""
                except Exception as e:
                    ep_color = f"[err:{e}]"
        except Exception as e:
            log(f"        row #{idx}: sig lookup fail: {e}")

        ep_state = classify_ep(ep_cls, ep_color)
        pdf_ok = is_pdf(name)
        contract_ok = is_target_contract(name)

        rows.append({
            "name": name,
            "ep_state": ep_state,
            "is_pdf": pdf_ok,
            "is_contract": contract_ok,
        })

        log(f"        row #{idx}: name={name!r} ep_state={ep_state} "
            f"pdf={pdf_ok} contract={contract_ok}")

    return rows


# ============================== ОБРАБОТКА ДОМА ==============================
def process_house(driver, wait, gis_handle, org_uk, index, total_links):
    links = driver.find_elements(
        By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]"
    )
    if index >= len(links):
        return False

    log(f"    [{index+1}/{total_links}] открываем карточку дома")

    card_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); "
            "arguments[0].click();", links[index]
        )
    )
    wait_for_page_fields(driver, timeout=10)
    time.sleep(0.3)

    addr = read_page_addr(driver)
    org = read_page_org(driver) or org_uk
    org = normalize_org(org)
    contract_date = read_contract_date(driver)
    log(f"      address = {addr!r}")
    log(f"      org     = {org!r}")
    log(f"      contract_date = {contract_date!r}")

    p_total, p_res, delta_str = build_delta_line(driver)
    log(f"      delta: total={p_total!r} res={p_res!r} delta={delta_str!r}")

    mgmt_links = driver.find_elements(
        By.XPATH, "//a[contains(., 'Информация об управлении МКД')]"
    )
    log(f"      ссылок 'Информация об управлении МКД': {len(mgmt_links)}")

    if not mgmt_links:
        log("      !!! NO_MGMT_PAGE")
        write_finding(
            org=org,
            addr=addr,
            pdf_label="Договор не размещен",
            date_label="Дата отсутствует",
            p_total=p_total, p_res=p_res, delta_str=delta_str,
            synopsis="информация о договоре и ЭП не размещена",
            marker="О",
        )
        close_tabs_except(driver, [gis_handle])
        driver.switch_to.window(gis_handle)
        return True

    mgmt_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script("arguments[0].click();", mgmt_links[0])
    )
    wait_for_page_fields(driver, timeout=10)
    ready = wait_ready_state(driver, timeout=15)
    log(f"      readyState complete → {ready}")
    ep_ok, total, classified, waited = wait_for_ep_rendered(driver, timeout=20)
    log(f"      wait_for_ep_rendered → ok={ep_ok} total={total} "
        f"classified={classified} waited={waited}s")

    time.sleep(0.3)

    addr2 = read_page_addr(driver)
    if addr2:
        addr = addr2
    org2 = read_page_org(driver)
    if org2:
        org = normalize_org(org2)
    contract_date2 = read_contract_date(driver)
    if contract_date2:
        contract_date = contract_date2
    log(f"      contract_date (после перехода) = {contract_date!r}")
    log(f"      org     (после перехода) = {org!r}")

    rows = scan_files(driver)
    pdf_rows = [r for r in rows if r["is_pdf"]]
    single_pdf = (len(pdf_rows) == 1)
    log(f"      pdf всего: {len(pdf_rows)}, single_pdf={single_pdf}")

    def _is_contract_for_no_ep(r):
        if not r["is_pdf"]:
            return False
        if r["is_contract"]:
            return True
        # правило одного PDF: если на странице единственный PDF —
        # считаем его договором, даже если имя не подошло под маску
        if single_pdf:
            return True
        return False

    targets_no_ep = [
        r for r in pdf_rows
        if _is_contract_for_no_ep(r) and r["ep_state"] == "absent"
    ]
    log(f"      целевых договоров без ЭП: {len(targets_no_ep)}")

    if targets_no_ep:
        pdf_name = targets_no_ep[0]["name"] or "Договор не подписан"
        date_label = contract_date or "Дата отсутствует"
        write_finding(
            org=org,
            addr=addr,
            pdf_label=pdf_name,
            date_label=date_label,
            p_total=p_total, p_res=p_res, delta_str=delta_str,
            synopsis="ЭП к договору отсутствует",
            marker="Э",
        )
    else:
        log("      дефекта NO_EP_CONTRACT нет")

    close_tabs_except(driver, [gis_handle])
    driver.switch_to.window(gis_handle)
    return True


# ============================== ПАГИНАЦИЯ ==============================
def next_page(driver):
    try:
        link = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(@ng-click, 'nextPage')]")
        ))
        before_links = _links_count(driver)
        driver.execute_script("arguments[0].click();", link)
        end = time.time() + 15
        while time.time() < end:
            time.sleep(0.5)
            after_links = _links_count(driver)
            if after_links != before_links:
                log(f"    nextPage: links {before_links} → {after_links}")
                time.sleep(1.5)
                return True
        log(f"    nextPage: кликнут, но links не изменились ({before_links})")
        return False
    except Exception as e:
        log(f"    nextPage: {type(e).__name__}: {e}")
        return False


# ============================== MAIN ==============================
def main():
    # затираем оба файла в начале прогона, чтобы каждый запуск был "с нуля"
    for p in (LOG_FILE, OUT_FILE):
        try:
            if p.exists():
                p.unlink()
        except Exception as e:
            print(f"[!] cannot remove {p}: {e}", flush=True)

    log("=" * 70)
    log(f"START scan_ep_dom.py v20 | OGRN={TARGET_OGRN}")
    log(f"LOG={LOG_FILE}")
    log(f"OUT={OUT_FILE}")
    log("=" * 70)

    driver = None
    try:
        driver = build_driver()
        wait = WebDriverWait(driver, 25)

        driver.get(GIS_URL)
        time.sleep(4)
        gis_handle = driver.current_window_handle
        log(f"    opened GIS, handle={gis_handle}")

        open_uk_tab(driver, wait)

        log(f"--- select org {TARGET_OGRN} ---")
        org_uk_raw = select_org(driver, wait, TARGET_OGRN)
        if not org_uk_raw:
            log("    OGRN not found / no cards — STOP")
            return
        set_page_size_100(driver, wait)

        total_records = read_total_records(driver)
        log(f"    Всего записей (со страницы): {total_records}")

        org_uk = clean_org_from_select2(org_uk_raw)
        org_uk = normalize_org(org_uk)
        log(f"    org_uk = {org_uk!r}")

        total_processed = 0
        page_no = 1

        while True:
            links = driver.find_elements(
                By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]"
            )
            total_links = len(links)
            log(f"--- страница {page_no}: домов на странице {total_links} "
                f"(заявлено всего {total_records}) ---")

            if total_links == 0:
                log("    домов нет — завершаем")
                break

            for idx in range(total_links):
                try:
                    process_house(driver, wait, gis_handle, org_uk, idx, total_links)
                    total_processed += 1
                except Exception as e:
                    log(f"    EXCEPTION на доме idx={idx}: "
                        f"{type(e).__name__}: {e}")
                    log(f"    TRACEBACK:\n{traceback.format_exc()}")
                    try:
                        close_tabs_except(driver, [gis_handle])
                        driver.switch_to.window(gis_handle)
                    except Exception:
                        pass

            if not next_page(driver):
                log("    nextPage не найден — завершаем")
                break
            page_no += 1

        log(f"ИТОГО обработано домов: {total_processed} "
            f"(заявлено {total_records})")
        if total_records > 0 and total_processed != total_records:
            log(f"!!! РАСХОЖДЕНИЕ: обработано {total_processed} "
                f"из {total_records}")
        log("STOP. Браузер оставлен открытым. Ctrl+C для выхода.")

        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            log("interrupted by user")

    except KeyboardInterrupt:
        log("interrupted by user (top)")
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        log(f"TRACEBACK:\n{traceback.format_exc()}")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()