# -*- coding: utf-8 -*-
"""
gisjkh-full.py — полный прогон по списку ОГРН из ogrns.txt:
    - вставка ОГРН в Select2 (с fallback и очисткой формы),
    - обработка каждого дома: карточка → ZIP → УФО,
    - 3 скриншота на дом с EXIF,
    - лог в %TEMP%\\IN\\LOG\\gisjkh-ufo.log,
    - секвенция scrNNNN.jpg в %TEMP%\\IN\\JPG\\.

Правило: обрабатываем дом, только если в архиве ровно 1 PDF + 1 P7S,
и имя PDF является частью имени P7S. Иначе — NOT_A_CONTRACT, пропуск.

Всё складывается в %TEMP%\\IN\\
    IN\\IN\\    — распаковка ZIP
    IN\\OLD\\   — архив обработанных PDF+P7S
    IN\\JPG\\   — секвенция скриншотов
    IN\\LOG\\   — лог и sequence.txt
    IN\\        — временные файлы
"""

import os
import re
import time
import random
import shutil
import subprocess
import datetime as _dt
from pathlib import Path
from tempfile import gettempdir

from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
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

COPYRIGHT = "Copyright 2026 \u00a9 damiurg by \u043c\u043b\u0445/\u05de\u05dc\u05da Living Private Trust"
TZ_NAME = "Asia/Omsk"
JPEG_QUALITY = 95

WAIT_TIMEOUT = 25
PAUSE_BETWEEN_PAGES = (2, 4)
PAUSE_BETWEEN_UK = (5, 10)

# ============================== PIEXIF ==============================
try:
    import piexif
    _HAS_PIEXIF = True
except ImportError:
    _HAS_PIEXIF = False
    print("[!] piexif не установлен. EXIF писаться не будет. "
          "Установи: pip install piexif")


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


# ============================== SNAPSHOT + EXIF ==============================
def snapshot(driver, tag: str, file_stem: str, extra: str = ""):
    try:
        n = _next_seq_number()
        scr_name = f"scr{n:04d}.jpg"
        out_path = JPG_DIR / scr_name

        tmp_png = JPG_DIR / f"_tmp_{n:04d}.png"
        ok = driver.save_screenshot(str(tmp_png))
        if not ok:
            print("[!] save_screenshot returned False")
            return

        exif_bytes = None
        if _HAS_PIEXIF:
            try:
                now = _dt.datetime.now(ZoneInfo(TZ_NAME))
                dt_str = now.strftime("%Y:%m:%d %H:%M:%S")
                tz_str = now.strftime("%z")
                tz_formatted = f"{tz_str[0]}{tz_str[1:3]}:{tz_str[3:5]}"
                exif_dict = {
                    "0th": {
                        piexif.ImageIFD.DateTime: dt_str.encode(),
                        piexif.ImageIFD.ImageDescription: driver.current_url.encode(),
                        piexif.ImageIFD.Copyright: COPYRIGHT.encode(),
                    },
                    "Exif": {
                        piexif.ExifIFD.DateTimeOriginal: dt_str.encode(),
                        piexif.ExifIFD.DateTimeDigitized: dt_str.encode(),
                        piexif.ExifIFD.OffsetTimeOriginal: tz_formatted.encode(),
                        piexif.ExifIFD.UserComment: extra.encode(),
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
    except Exception as e:
        print(f"[!] snapshot failed: {e}")


# ============================== ТАЙМСТАМП-ОВЕРЛЕЙ ==============================
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


# ============================== ВСПОМОГАТЕЛЬНОЕ (из test.py) ==============================
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


# ============================== ВСПОМОГАТЕЛЬНОЕ (из snapshot) ==============================
def clean_dir_files(directory: Path):
    if not directory.exists():
        return
    for item in directory.iterdir():
        if item.is_file():
            try:
                item.unlink()
            except Exception:
                pass


def wait_file(directory: Path, pattern: str, timeout: int = 30):
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


def find_pdf_p7s_pair(directory: Path):
    """
    Жёсткое правило:
      - в архиве ровно 1 PDF и ровно 1 P7S;
      - имя PDF является частью имени P7S.
    Иначе — пустой список (NOT_A_CONTRACT).
    """
    pdfs = [f for f in directory.iterdir() if f.suffix.lower() == ".pdf"]
    p7s_list = [f for f in directory.iterdir()
                if f.suffix.lower() in (".p7s", ".sig", ".sgn")]

    if len(pdfs) != 1 or len(p7s_list) != 1:
        return []

    pdf = pdfs[0]
    p7s = p7s_list[0]

    if pdf.name not in p7s.name:
        return []

    return [(pdf, p7s)]


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


def close_tabs_except(driver, keep_handles: list):
    for h in driver.window_handles:
        if h not in keep_handles:
            driver.switch_to.window(h)
            driver.close()


def open_new_tab_and_switch(driver, wait, click_fn):
    handles_before = set(driver.window_handles)
    click_fn()
    wait.until(lambda d: len(set(d.window_handles) - handles_before) > 0)
    new_handle = (set(driver.window_handles) - handles_before).pop()
    driver.switch_to.window(new_handle)
    time.sleep(2)
    inject_timestamp_overlay(driver)
    return new_handle


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
        select_el = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "select#count")
        ))
        Select(select_el).select_by_value("100")
        time.sleep(2)
        return True
    except Exception as e:
        log(f"    cannot set page size 100: {e}")
        return False


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


# ============================== ОСНОВНОЙ ЦИКЛ ==============================
def process_one_house(driver, ufo_handle, gis_handle, index):
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

    mgmt_link = wait.until(EC.presence_of_element_located(
        (By.XPATH, "//a[contains(., 'Информация об управлении МКД')]")
    ))
    time.sleep(1)
    mgmt_handle = open_new_tab_and_switch(
        driver, wait,
        lambda: driver.execute_script("arguments[0].click();", mgmt_link)
    )

    contract_date = read_contract_date(driver)

    # --- ПРАВКА 1: задержка 2 сек перед скриншотом 01_mkd ---
    time.sleep(2)

    download_btn = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "button[class*='downloadAllFiles']")
    ))

    snapshot(driver, "01_mkd", "pending",
             f"МКД, дата ДУ(У)={contract_date}")
    time.sleep(0.5)

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

    driver.switch_to.window(ufo_handle)
    inject_timestamp_overlay(driver)
    set_ufo_zoom(driver)

    pairs = find_pdf_p7s_pair(IN_DIR)
    if not pairs:
        log("    NOT_A_CONTRACT: в архиве не 1 PDF + 1 P7S, пропускаем дом")
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

    snapshot(driver, "02_ufo_loaded", file_stem,
             f"{file_stem} | ДУ(У)={contract_date}")
    time.sleep(0.5)

    check_btn = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "button[type='submit'].white.button")
    ))
    check_btn.click()

    result_sign = "-"
    try:
        wait.until(EC.presence_of_element_located(
            (By.XPATH,
             "//*[contains(normalize-space(.), "
             "'Отчет о проверке квалифицированной электронной подписи')]")
        ))
        time.sleep(2)

        snapshot(driver, "03_ufo_result", file_stem,
                 f"{file_stem} | ДУ(У)={contract_date} | результат={result_sign}")
        time.sleep(0.5)

        result_sign = "+"
    except Exception:
        result_sign = "-"

    try:
        back_link = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(., 'Назад')]")
        ))
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
    """Дефолтный Chrome, без binary_location."""
    options = webdriver.ChromeOptions()
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