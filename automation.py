from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pyautogui
import pyperclip

from config import normalize_key_name
from db import TrackerDatabase


StatusCallback = Callable[[str], None]
ResultCallback = Callable[[int, str, dict[str, Any]], None]
OcrFallbackCallback = Callable[[int], str]


def sleep_with_stop(seconds: float, stop_event: threading.Event) -> bool:
    end_time = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end_time:
        if stop_event.is_set():
            return False
        time.sleep(min(0.05, end_time - time.monotonic()))
    return not stop_event.is_set()


def press_key(key_name: str) -> None:
    key = normalize_key_name(key_name)
    pyautogui.press(key)


def key_down(key_name: str) -> str:
    key = normalize_key_name(key_name)
    pyautogui.keyDown(key)
    return key


def key_up(key_name: str) -> None:
    pyautogui.keyUp(normalize_key_name(key_name))


def preview_text(value: str, limit: int = 90) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "(пусто)"
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def copy_selected_text(
    stop_event: threading.Event,
    after_copy_delay_sec: float,
    timeout_sec: float,
) -> str:
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "c")

    end_time = time.monotonic() + max(0.0, timeout_sec)
    first_delay_done = False
    last_value = ""
    while time.monotonic() <= end_time:
        if stop_event.is_set():
            return last_value
        if not first_delay_done:
            if not sleep_with_stop(after_copy_delay_sec, stop_event):
                return last_value
            first_delay_done = True
        else:
            time.sleep(0.05)

        last_value = pyperclip.paste()
        if str(last_value).strip():
            return last_value

    return last_value


def focus_dota_window(config: dict[str, Any], on_status: StatusCallback | None = None) -> bool:
    if not bool(config.get("focus_dota_on_start", True)):
        return True

    titles = [
        str(title).strip()
        for title in config.get("dota_window_titles", ["Dota 2", "Dota2"])
        if str(title).strip()
    ]
    if not titles:
        titles = ["Dota 2", "Dota2"]

    try:
        windows = []
        for title in titles:
            windows.extend(pyautogui.getWindowsWithTitle(title))
        seen = set()
        for window in windows:
            key = getattr(window, "_hWnd", None) or id(window)
            if key in seen:
                continue
            seen.add(key)
            if getattr(window, "isMinimized", False):
                window.restore()
                time.sleep(0.2)
            window.activate()
            time.sleep(0.35)
            if on_status:
                on_status(f"Окно Dota 2 найдено и активировано: {window.title}")
            return True
    except Exception as exc:
        if on_status:
            on_status(f"Не удалось переключиться на Dota 2: {exc}")
        return False

    if on_status:
        on_status("Окно Dota 2 не найдено, продолжаю без переключения.")
    return False


def open_player_slot(
    config: dict[str, Any],
    slot: dict[str, Any],
    index: int,
    stop_event: threading.Event,
    on_status: StatusCallback | None = None,
) -> bool:
    slot_no = int(slot.get("slot", index))
    x = int(slot.get("x", 0))
    y = int(slot.get("y", 0))
    list_key = str(config.get("list_key", "tab"))

    if on_status:
        on_status(f"Слот {slot_no}: держу кнопку списка и открываю профиль.")

    list_key_pressed = ""
    clicked_slot = False
    try:
        list_key_pressed = key_down(list_key)
        if not sleep_with_stop(float(config.get("after_list_delay_sec", 0.35)), stop_event):
            return False

        pyautogui.click(x, y)
        clicked_slot = True
        time.sleep(max(0.0, float(config.get("release_list_after_slot_click_sec", 0.015))))
    finally:
        if list_key_pressed:
            key_up(list_key_pressed)

    return clicked_slot and not stop_event.is_set()


def collect_players(
    config: dict[str, Any],
    db: TrackerDatabase,
    stop_event: threading.Event,
    on_status: StatusCallback | None = None,
    on_result: ResultCallback | None = None,
    on_empty_clipboard_ocr: OcrFallbackCallback | None = None,
) -> None:
    def status(message: str) -> None:
        if on_status:
            on_status(message)

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.03

    slots = config.get("slots", [])
    drag = config.get("id_drag", {})
    back = config.get("back_button", {})

    focused = focus_dota_window(config, status)
    if focused:
        status("Старт сбора: Dota 2 активна, жду перед кликами.")
    else:
        status("Старт сбора: проверь фокус Dota 2, жду перед кликами.")
    if not sleep_with_stop(float(config.get("start_delay_sec", 2.0)), stop_event):
        status("Сбор остановлен до старта.")
        return

    for index, slot in enumerate(slots, start=1):
        if stop_event.is_set():
            break

        slot_no = int(slot.get("slot", index))
        if not open_player_slot(config, slot, index, stop_event, status):
            break

        if not sleep_with_stop(float(config.get("after_player_click_delay_sec", 0.8)), stop_event):
            break

        status(f"Слот {slot_no}: выделяю Steam ID.")
        start_x = int(drag.get("start_x", 658))
        start_y = int(drag.get("start_y", 201))
        end_x = int(drag.get("end_x", 741))
        end_y = int(drag.get("end_y", 201))
        duration = float(config.get("drag_duration_sec", 0.25))
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=duration, button="left")
        if not sleep_with_stop(float(config.get("after_drag_delay_sec", 0.2)), stop_event):
            break

        copied = copy_selected_text(
            stop_event,
            float(config.get("after_copy_delay_sec", 0.25)),
            float(config.get("clipboard_wait_timeout_sec", 1.0)),
        )
        if stop_event.is_set():
            break

        status(f"Слот {slot_no}: буфер после Ctrl+C = {preview_text(copied)!r}.")
        if not str(copied).strip() and bool(config.get("use_ocr_when_clipboard_empty", True)):
            if on_empty_clipboard_ocr:
                status(f"Слот {slot_no}: буфер пустой, пробую OCR по зоне ID.")
                copied = on_empty_clipboard_ocr(slot_no)
                status(f"Слот {slot_no}: OCR fallback = {preview_text(copied)!r}.")
            else:
                status(f"Слот {slot_no}: OCR fallback недоступен.")

        result = db.upsert_player(copied)
        if on_result:
            on_result(slot_no, copied, result)

        status(f"Слот {slot_no}: возвращаюсь к списку.")
        pyautogui.click(int(back.get("x", 32)), int(back.get("y", 34)))
        if not sleep_with_stop(float(config.get("after_back_delay_sec", 0.35)), stop_event):
            break

    if stop_event.is_set():
        status("Сбор остановлен.")
    else:
        status("Сбор завершен.")


class KeyboardHotkey:
    def __init__(self, hotkey: str, callback: Callable[[], None]):
        self.hotkey = hotkey
        self.callback = callback
        self._handle: Any = None
        self._backend = ""

    def start(self) -> None:
        self.stop()
        hotkey = (self.hotkey or "").strip().lower()
        if not hotkey:
            return

        try:
            import keyboard

            self._handle = keyboard.add_hotkey(hotkey, self.callback)
            self._backend = "keyboard"
            return
        except Exception:
            self._handle = None
            self._backend = ""

        try:
            from pynput import keyboard as pynput_keyboard

            spec = _to_pynput_hotkey(hotkey)
            listener = pynput_keyboard.GlobalHotKeys({spec: self.callback})
            listener.start()
            self._handle = listener
            self._backend = "pynput"
        except Exception:
            self._handle = None
            self._backend = ""

    def stop(self) -> None:
        if not self._handle:
            return
        try:
            if self._backend == "keyboard":
                import keyboard

                keyboard.remove_hotkey(self._handle)
            elif self._backend == "pynput":
                self._handle.stop()
        finally:
            self._handle = None
            self._backend = ""


def _to_pynput_hotkey(hotkey: str) -> str:
    parts = [part.strip().lower() for part in hotkey.replace(" ", "").split("+") if part.strip()]
    converted: list[str] = []
    named = {"ctrl", "shift", "alt", "cmd", "tab", "esc", "enter", "space"}
    function_keys = {f"f{i}" for i in range(1, 25)}
    for part in parts:
        if part in function_keys or part in named:
            converted.append(f"<{part}>")
        else:
            converted.append(part)
    return "+".join(converted)
