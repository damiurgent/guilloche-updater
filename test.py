# -*- coding: utf-8 -*-
"""
test_passport2.py — диагностика электронного паспорта дома с retry.

Логика:
  1. ГИС → поиск по ОГРН → первый дом.
  2. Клик «Посмотреть электронные паспорта дома» → модалка «Нет/Да» → «Да».
  3. Переключение на новую вкладку с паспортом.
  4. Ждём появления КОНКРЕТНО поля "2.15." в DOM.
  5. Парсинг 2.15 / 2.15.1 / 2.15.2 / 2.15.3.
  6. Если паспорт пустой — закрываем вкладку, повторяем ЕЩЁ РАЗ (2 попытки max).
  7. Если и вторая пустая — оставляем попытки.

ВАЖНО: селектор ищет `td.attr-body-td-param-text` (а не `attr-body-td-node-text`).
Класс `attr-body-td-node-text` Angular навешивает только на строки-родители
(у которых есть дети, например 2.15). У строк 2.15.1/2.15.2/2.15.3 этого
класса НЕТ, поэтому старый селектор их не находил.

Ничего не пишет в файлы. Только stdout.
Браузер не закрывает до нажатия Enter.
"""

import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ============================== НАСТРОЙКИ ==============================
GIS_URL = "https://my.dom.gosuslugi.ru/#!/houses"
OGRN_FILE = Path(__file__).resolve().parent / "ogrns.txt"
OGRN_FALLBACK = "1055513003204"

WAIT_TIMEOUT = 25

# ============================== RETRY ==============================
MAX_PASSPORT_ATTEMPTS = 2       # первая + одна повторная
PASSPORT_RENDER_TIMEOUT = 30    # сколько ждём появления 2.15 в новой вкладке
PAUSE_BEFORE_RETRY = 2          # пауза перед повторным кликом


# ============================== ЛОГ ==============================
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def read_first_ogrn() -> str:
    if OGRN_FILE.exists():
        for line in OGRN_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if re.fullmatch(r"\d{13}", s):
                return s
    return OGRN_FALLBACK


# ============================== ДРАЙВЕР ==============================
def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    return driver


# ============================== ВСПОМОГАТЕЛЬНОЕ ==============================
def drop_select2_mask(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll('.select2-drop-mask').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop-active').forEach(e => e.remove());
            document.querySelectorAll('.select2-drop').forEach(e => e.remove());
        """)
    except Exception:
        pass


def _close_current_tab(driver, keep_handle):
    """Закрывает текущую вкладку и возвращается в keep_handle."""
    try:
        driver.close()
    except Exception as e:
        log(f"    close() failed: {e}")
    time.sleep(0.5)
    try:
        driver.switch_to.window(keep_handle)
    except Exception as e:
        log(f"    switch_to({keep_handle}) failed: {e}")


def snapshot(driver, tag: str):
    """Короткая сводка по текущей странице — без сохранения файлов."""
    body = ""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        pass
    has_215 = "2.15" in body
    has_pl = "Площадь здания" in body
    log(f"  [{tag}] body.len={len(body)} "
        f"has_2.15={has_215} has_Площадь_здания={has_pl} "
        f"url={driver.current_url} handles={len(driver.window_handles)}")


# ============================== ПАРСИНГ ==============================
def _parse_float(s):
    if not s:
        return None
    s = s.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _label_text(lbl) -> str:
    """Универсальное чтение текста label: textContent -> text."""
    try:
        t = lbl.get_attribute("textContent")
        if t is not None:
            return t
    except Exception:
        pass
    try:
        return lbl.text or ""
    except Exception:
        return ""


def _has_215_in_dom(driver) -> bool:
    """
    Есть ли в текущем DOM строка именно '2.15. ...' (не 2.15.1/2/3).
    Ищем label внутри td.attr-body-td-param-text (этот класс есть
    у ВСЕХ строк, в отличие от attr-body-td-node-text).
    """
    try:
        labels = driver.find_elements(
            By.CSS_SELECTOR,
            "td.attr-body-td-param-text label.form-base__control-label"
        )
        for lbl in labels:
            txt = _label_text(lbl).strip()
            if not txt.startswith("2.15."):
                continue
            if len(txt) > 5 and txt[5].isdigit():
                # 2.15.1. / 2.15.2. / 2.15.3. — не то
                continue
            return True
        return False
    except Exception:
        return False


def grab_passport(driver) -> dict:
    """
    Возвращает {} если 2.15 не найден (паспорт пустой).
    Иначе {'p215', 'p2151', 'p2152', 'p2153', 'delta'}.
    """
    result = {"p215": None, "p2151": None, "p2152": None, "p2153": None, "delta": None}

    def _grab(label_text: str):
        log(f"    _grab({label_text}): старт")
        try:
            labels = driver.find_elements(
                By.CSS_SELECTOR,
                "td.attr-body-td-param-text label.form-base__control-label"
            )
            log(f"    _grab({label_text}): найдено label с классом "
                f"form-base__control-label внутри td.attr-body-td-param-text: {len(labels)}")
            if not labels:
                log(f"    _grab({label_text}): нет ни одного label — выходим")
                return None
            for idx, lbl in enumerate(labels):
                try:
                    txt_raw = _label_text(lbl)
                except Exception as e:
                    log(f"    _grab({label_text}): [{idx}] чтение текста упало: {e}")
                    continue
                txt = txt_raw.strip()
                if not txt:
                    continue
                if not txt.startswith(label_text):
                    continue
                if label_text == "2.15." and len(txt) > 5 and txt[5].isdigit():
                    continue
                log(f"    _grab({label_text}): [{idx}] подходящая метка: {txt[:80]!r}")
                try:
                    tr = lbl.find_element(By.XPATH, "./ancestor::tr")
                except Exception as e:
                    log(f"    _grab({label_text}): [{idx}] tr не найден: {e}")
                    continue
                try:
                    val_labels = tr.find_elements(
                        By.CSS_SELECTOR,
                        "td.attr-body-td-param-value label.form-base__control-label"
                    )
                except Exception as e:
                    log(f"    _grab({label_text}): [{idx}] val_labels упал: {e}")
                    continue
                log(f"    _grab({label_text}): [{idx}] val_labels найдено: {len(val_labels)}")
                if not val_labels:
                    continue
                for j, vl in enumerate(reversed(val_labels)):
                    try:
                        v_raw = _label_text(vl)
                    except Exception as e:
                        log(f"    _grab({label_text}): [{idx}] reversed[{j}] чтение упало: {e}")
                        continue
                    v = v_raw.strip()
                    log(f"    _grab({label_text}): [{idx}] reversed[{j}] v={v!r}")
                    if v and re.match(r"^[\d\s.,]+$", v):
                        log(f"    _grab({label_text}): [{idx}] -> выбрали {v!r}")
                        return v
                log(f"    _grab({label_text}): [{idx}] в этом tr нет подходящего числа")
            log(f"    _grab({label_text}): ничего не найдено, возвращаем None")
        except Exception as e:
            log(f"    _grab({label_text}) EXCEPTION: {e}")
        return None

    result["p215"]  = _parse_float(_grab("2.15."))
    result["p2151"] = _parse_float(_grab("2.15.1."))
    result["p2152"] = _parse_float(_grab("2.15.2."))
    result["p2153"] = _parse_float(_grab("2.15.3."))

    log(f"    grab_passport: p215={result['p215']} p2151={result['p2151']} "
        f"p2152={result['p2152']} p2153={result['p2153']}")

    if None in (result["p215"], result["p2151"], result["p2152"], result["p2153"]):
        return {}

    result["delta"] = (result["p2151"] + result["p2152"] + result["p2153"]) - result["p215"]
    return result


# ============================== ОДНА ПОПЫТКА ==============================
def try_open_passport(driver, card_handle, attempt: int):
    """
    Одна попытка:
      1) вернуться в карточку дома,
      2) клик по ссылке паспорта,
      3) клик «Да» (bubbles:false — не всплывает до close($event)),
      4) переключение на новую вкладку,
      5) ожидание появления ИМЕННО 2.15 в DOM,
      6) парсинг.

    Возвращает:
      - ('ok', data)             — паспорт сформирован, data непустой
      - ('empty', None)          — вкладка открылась, но 2.15 не появилось
      - ('no_tab', None)         — новая вкладка не открылась
      - ('error', str)           — что-то упало
    """
    log(f"--- passport attempt {attempt}/{MAX_PASSPORT_ATTEMPTS} ---")

    try:
        # 1. Возврат в карточку дома
        try:
            driver.switch_to.window(card_handle)
        except Exception as e:
            return ("error", f"switch_to(card_handle): {e}")
        time.sleep(1)

        # 2. Ссылка «Посмотреть электронные паспорта дома»
        try:
            passport_link = WebDriverWait(driver, 10).until(EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@ng-click, 'showPassport')]")
            ))
        except TimeoutException:
            return ("error", "passport link not found")

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); "
            "arguments[0].click();",
            passport_link
        )
        log("  passport link clicked")
        time.sleep(1)

        # 3. Клик «Да» — bubbles:false
        handles_before_yes = set(driver.window_handles)
        try:
            ok = driver.execute_script("""
                var b = document.querySelector("button.btn-action[ng-click*='yes']");
                if (!b) return 'no_button';
                b.dispatchEvent(new MouseEvent('click', {
                    bubbles: false, cancelable: true, view: window
                }));
                return 'ok';
            """)
            log(f"  click 'Да' -> {ok}")
            if ok != 'ok':
                return ("error", f"yes button not clicked: {ok}")
        except Exception as e:
            return ("error", f"click yes failed: {e}")

        # 4. Ожидание новой вкладки
        try:
            WebDriverWait(driver, 20).until(
                lambda d: len(d.window_handles) > len(handles_before_yes)
            )
        except TimeoutException:
            return ("no_tab", None)

        new_handles = [h for h in driver.window_handles if h not in handles_before_yes]
        passport_handle = new_handles[-1]
        driver.switch_to.window(passport_handle)
        log(f"  switched to passport tab: {driver.current_url}")

        # 5. Ждём появления ИМЕННО 2.15 в DOM
        try:
            WebDriverWait(driver, PASSPORT_RENDER_TIMEOUT).until(
                lambda d: _has_215_in_dom(d)
            )
            log("  2.15. появился в DOM")
        except TimeoutException:
            log(f"  2.15. не появился за {PASSPORT_RENDER_TIMEOUT} сек (пусто)")
            snapshot(driver, f"empty_a{attempt}")
            _close_current_tab(driver, card_handle)
            return ("empty", None)

        # 6. Парсинг
        data = grab_passport(driver)
        if data and data.get("p215") is not None:
            snapshot(driver, f"ok_a{attempt}")
            _close_current_tab(driver, card_handle)
            return ("ok", data)
        else:
            log("  2.15 в DOM, но значение не извлеклось — считаем пустым")
            snapshot(driver, f"empty_a{attempt}")
            _close_current_tab(driver, card_handle)
            return ("empty", None)

    except Exception as e:
        log(f"  attempt {attempt} EXCEPTION: {type(e).__name__}: {e}")
        # прибираем лишние вкладки
        try:
            for h in list(driver.window_handles):
                if h != card_handle:
                    try:
                        driver.switch_to.window(h)
                        driver.close()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            driver.switch_to.window(card_handle)
        except Exception:
            pass
        return ("error", str(e))


# ============================== MAIN ==============================
def main():
    ogrn = read_first_ogrn()
    log(f"=== test_passport2 START === OGRN={ogrn}")

    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        # ГИС ЖКХ
        driver.get(GIS_URL)
        time.sleep(3)

        # Вкладка «Поиск дома по УО»
        tab = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(., 'Поиск дома по управляющей организации')]")
        ))
        tab.click()
        time.sleep(2)

        # Select2: ОГРН
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
        search_input.clear()
        search_input.send_keys(ogrn)
        time.sleep(1.5)

        result_link = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR,
             "ul.select2-results li.select2-result-selectable a, "
             "ul.select2-results li.select2-result-selectable")
        ))
        result_link.click()
        time.sleep(2)

        find_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[normalize-space(.)='Найти']")
        ))
        find_btn.click()
        time.sleep(2)

        # Первый дом
        links = wait.until(EC.presence_of_all_elements_located(
            (By.XPATH, "//a[contains(., 'Сведения об объекте жилищного фонда')]")
        ))
        log(f"house cards: {len(links)}")

        handles_before_card = set(driver.window_handles)
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); "
            "arguments[0].click();",
            links[0]
        )
        wait.until(lambda d: len(set(d.window_handles) - handles_before_card) > 0)
        card_handle = (set(driver.window_handles) - handles_before_card).pop()
        driver.switch_to.window(card_handle)
        time.sleep(3)
        log(f"card opened: url={driver.current_url} handles={len(driver.window_handles)}")

        # Попытки открыть паспорт
        passport_data = {}
        attempt_results = []
        for attempt in range(1, MAX_PASSPORT_ATTEMPTS + 1):
            status, payload = try_open_passport(driver, card_handle, attempt)
            attempt_results.append((attempt, status, payload))

            if status == "ok":
                passport_data = payload
                log(f"=== PASSPORT FOUND on attempt {attempt}: {passport_data} ===")
                break

            if status == "empty":
                if attempt < MAX_PASSPORT_ATTEMPTS:
                    log(f"attempt {attempt}: пусто, повторяем через {PAUSE_BEFORE_RETRY} сек")
                    time.sleep(PAUSE_BEFORE_RETRY)
                else:
                    log(f"attempt {attempt}: пусто, попытки закончились")
            elif status == "no_tab":
                log(f"attempt {attempt}: новая вкладка не открылась")
            else:
                log(f"attempt {attempt}: ошибка — {payload}")

        # Итог
        log("---- ИТОГ ----")
        for attempt, status, payload in attempt_results:
            log(f"  попытка {attempt}: {status} {payload if payload else ''}")
        if passport_data:
            log(f"PASSPORT: {passport_data}")
        else:
            log("PASSPORT NOT FORMED (все попытки пустые/ошибочные)")

        log("---- DONE. Браузер оставлен открытым. ----")

    except Exception as e:
        log(f"!!! EXCEPTION: {type(e).__name__}: {e}")
        try:
            snapshot(driver, "exception")
        except Exception:
            pass

    finally:
        try:
            input("Нажми Enter, чтобы закрыть браузер... ")
        except EOFError:
            pass
        try:
            driver.quit()
        except Exception:
            pass
        log("browser closed")


if __name__ == "__main__":
    main()