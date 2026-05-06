from __future__ import annotations

import copy
import sys
import threading
import timeit  # noqa: F401 - needed by external torch when PyInstaller excludes torch itself
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocr_service import prepare_ocr_runtime


def _project_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_OCR_DIR = _project_base_dir() / "ocr"
TORCH_PRELOAD_ERROR = ""
try:
    prepare_ocr_runtime(PROJECT_OCR_DIR)
    import torch  # noqa: F401

    try:
        torch.set_num_threads(1)
    except Exception:
        pass
except Exception as exc:
    TORCH_PRELOAD_ERROR = str(exc)

import pyautogui
import pyperclip
from PyQt5.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from automation import KeyboardHotkey, collect_players, focus_dota_window, open_player_slot, sleep_with_stop
from config import CONFIG_PATH, DB_PATH, OCR_DIR, load_config, normalize_key_name, save_config
from db import TrackerDatabase
from ocr_service import OcrDependencyError, OcrService, find_exact_digit_matches
from steam_profile import fetch_steam_profile_level, steam_profile_url_from_id


def resource_path(name: str) -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir / name


def app_icon() -> QIcon:
    for name in ("app_icon.ico", "icon.ico", "icon.png"):
        path = resource_path(name)
        if path.exists():
            return QIcon(str(path))
    return QIcon()


DARK_STYLE = """
QMainWindow, QWidget {
    background: #050607;
    color: #e8eef2;
    font-size: 10pt;
}
QStatusBar {
    background: #090b0d;
    color: #9fb0bd;
    border-top: 1px solid #202832;
}
QTabWidget::pane {
    border: 1px solid #202832;
    border-radius: 8px;
    background: #090b0d;
}
QTabBar::tab {
    background: #10151b;
    color: #aeb9c2;
    padding: 9px 16px;
    border: 1px solid #202832;
    border-bottom: 0;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected {
    background: #18212b;
    color: #ffffff;
}
QGroupBox {
    background: #090b0d;
    border: 1px solid #202832;
    border-radius: 8px;
    margin-top: 18px;
    padding: 14px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #75d3ff;
}
QPushButton {
    background: #17212b;
    color: #f1f6f9;
    border: 1px solid #2f4052;
    border-radius: 7px;
    padding: 8px 13px;
}
QPushButton:hover {
    background: #1f2d3a;
    border-color: #4c8db3;
}
QPushButton:pressed {
    background: #0f8bc9;
}
QPushButton:disabled {
    background: #11161b;
    color: #586570;
    border-color: #1b222a;
}
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #0d1116;
    color: #ffffff;
    border: 1px solid #27323e;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #0f8bc9;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #75d3ff;
}
QTableWidget {
    background: #07090b;
    alternate-background-color: #0c1116;
    gridline-color: #202832;
    border: 1px solid #202832;
    border-radius: 8px;
    selection-background-color: #0d5678;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #111820;
    color: #d6e2ea;
    border: 0;
    border-right: 1px solid #202832;
    border-bottom: 1px solid #202832;
    padding: 8px;
}
QScrollArea {
    border: 0;
}
QScrollBar:vertical {
    background: #07090b;
    width: 12px;
}
QScrollBar::handle:vertical {
    background: #24313d;
    border-radius: 6px;
}
QLabel {
    color: #d6e2ea;
}
QCheckBox {
    spacing: 8px;
}
"""


LOW_LEVEL_ROLE = Qt.UserRole + 20


class LowLevelOutlineDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if not index.data(LOW_LEVEL_ROLE):
            return

        painter.save()
        pen = QPen(QColor("#ff334d"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
        painter.restore()


class RegionSelector(QWidget):
    region_selected = pyqtSignal(dict)
    cancelled = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setCursor(Qt.CrossCursor)
        self.start_pos: QPoint | None = None
        self.current_pos: QPoint | None = None
        self.setGeometry(self._desktop_geometry())

    def _desktop_geometry(self) -> QRect:
        desktop = QApplication.desktop()
        geometry = QRect()
        for index in range(desktop.screenCount()):
            geometry = geometry.united(desktop.screenGeometry(index))
        return geometry

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.current_pos = event.pos()
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self.start_pos is not None:
            self.current_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() != Qt.LeftButton or self.start_pos is None:
            return
        self.current_pos = event.pos()
        rect = QRect(self.start_pos, self.current_pos).normalized()
        if rect.width() >= 3 and rect.height() >= 3:
            origin = self.geometry().topLeft()
            self.region_selected.emit(
                {
                    "x": rect.x() + origin.x(),
                    "y": rect.y() + origin.y(),
                    "width": rect.width(),
                    "height": rect.height(),
                }
            )
        else:
            self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))

        if self.start_pos is not None and self.current_pos is not None:
            rect = QRect(self.start_pos, self.current_pos).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#75d3ff"), 2))
            painter.drawRect(rect)

        painter.setPen(QPen(QColor("#f1f6f9"), 1))
        painter.drawText(24, 36, "Зажми ЛКМ и выдели OCR-область. Esc — отмена.")


class HotkeyEdit(QLineEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText("Кликни и нажми клавишу")
        self._capturing = False

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._capturing = True
        self.setText("Нажми клавишу...")
        self.selectAll()
        super().mousePressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._capturing = True
        self.setText("Нажми клавишу...")
        self.selectAll()
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        key_name = self._event_to_hotkey(event)
        if not key_name:
            return
        self._capturing = False
        self.setText(key_name)
        self.clearFocus()

    def event(self, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() in {Qt.Key_Tab, Qt.Key_Backtab}:
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def _event_to_hotkey(self, event) -> str:
        key = event.key()
        if key in {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta}:
            return ""
        if key in {Qt.Key_Backspace, Qt.Key_Delete}:
            return ""

        parts: list[str] = []
        modifiers = event.modifiers()
        if modifiers & Qt.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.AltModifier:
            parts.append("alt")
        if modifiers & Qt.ShiftModifier:
            parts.append("shift")

        special = {
            Qt.Key_Tab: "tab",
            Qt.Key_Backtab: "tab",
            Qt.Key_Escape: "esc",
            Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter",
            Qt.Key_Space: "space",
            Qt.Key_QuoteLeft: "~",
        }
        if Qt.Key_F1 <= key <= Qt.Key_F24:
            key_text = f"f{key - Qt.Key_F1 + 1}"
        elif key in special:
            key_text = special[key]
        elif event.text().strip():
            key_text = event.text().strip().lower()
        else:
            return ""

        parts.append(key_text)
        return "+".join(parts)


class MainWindow(QMainWindow):
    status_signal = pyqtSignal(str)
    refresh_signal = pyqtSignal()
    collect_state_signal = pyqtSignal(bool)
    hotkey_signal = pyqtSignal()
    stop_hotkey_signal = pyqtSignal()
    clipboard_signal = pyqtSignal(str)
    ocr_match_signal = pyqtSignal(object)
    ocr_done_signal = pyqtSignal(object)
    level_state_signal = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Dota 2 Steam ID Tracker")
        self.setWindowIcon(app_icon())
        self.resize(1120, 760)

        self.config = load_config()
        self.db = TrackerDatabase(DB_PATH)
        self.stop_event = threading.Event()
        self.collect_thread: threading.Thread | None = None
        self.ocr_thread: threading.Thread | None = None
        self.level_thread: threading.Thread | None = None
        self.start_hotkey_handle: KeyboardHotkey | None = None
        self.stop_hotkey_handle: KeyboardHotkey | None = None
        self.region_selector: RegionSelector | None = None
        self.toast_notifier: Any = None
        self.loading_players = False
        self.loading_ignored = False

        self._build_ui()
        self._load_config_to_widgets()
        self._wire_signals()
        self.refresh_all()
        self.set_collecting(False)
        self._register_hotkey()
        QTimer.singleShot(700, self._auto_check_levels_on_start)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        self.collect_btn = QPushButton("Собрать игроков")
        self.stop_btn = QPushButton("Стоп")
        self.ocr_btn = QPushButton("OCR проверка")
        self.clipboard_btn = QPushButton("Проверить буфер")
        self.save_btn = QPushButton("Сохранить настройки")
        self.status_label = QLabel("Готово")
        self.last_clipboard_edit = QLineEdit()
        self.last_clipboard_edit.setReadOnly(True)
        self.last_clipboard_edit.setPlaceholderText("Последний текст из clipboard")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        controls.addWidget(self.collect_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.ocr_btn)
        controls.addWidget(self.clipboard_btn)
        controls.addWidget(self.save_btn)
        controls.addStretch(1)
        controls.addWidget(QLabel("Буфер:"))
        controls.addWidget(self.last_clipboard_edit, 2)
        controls.addWidget(self.status_label, 3)
        main_layout.addLayout(controls)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_players_tab(), "Игроки")
        self.tabs.addTab(self._build_ignored_tab(), "Игнор ID")
        self.tabs.addTab(self._build_settings_tab(), "Настройки")
        main_layout.addWidget(self.tabs)

    def _build_players_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        self.add_player_btn = QPushButton("Добавить ID")
        self.open_profile_btn = QPushButton("Открыть Steam профиль")
        self.copy_profile_btn = QPushButton("Скопировать ссылку профиля")
        self.check_levels_btn = QPushButton("Проверить уровни")
        self.delete_player_btn = QPushButton("Удалить выбранное")
        self.refresh_players_btn = QPushButton("Обновить")
        row.addWidget(self.add_player_btn)
        row.addWidget(self.open_profile_btn)
        row.addWidget(self.copy_profile_btn)
        row.addWidget(self.check_levels_btn)
        row.addWidget(self.delete_player_btn)
        row.addWidget(self.refresh_players_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.players_table = QTableWidget(0, 6)
        self.players_table.setHorizontalHeaderLabels(
            ["Steam ID", "Уровень", "Заметка / описание", "Добавлен", "Последний раз", "Встреч"]
        )
        self.players_table.setItemDelegate(LowLevelOutlineDelegate(self.players_table))
        self.players_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.players_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.players_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.players_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.players_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.players_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.players_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.players_table.setAlternatingRowColors(True)
        layout.addWidget(self.players_table)
        return tab

    def _build_ignored_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        row = QHBoxLayout()
        self.add_ignored_btn = QPushButton("Добавить в игнор")
        self.delete_ignored_btn = QPushButton("Удалить из игнора")
        self.refresh_ignored_btn = QPushButton("Обновить")
        row.addWidget(self.add_ignored_btn)
        row.addWidget(self.delete_ignored_btn)
        row.addWidget(self.refresh_ignored_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.ignored_table = QTableWidget(0, 3)
        self.ignored_table.setHorizontalHeaderLabels(["ID / текст", "Заметка", "Добавлен"])
        self.ignored_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.ignored_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ignored_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.ignored_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ignored_table.setAlternatingRowColors(True)
        layout.addWidget(self.ignored_table)
        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        hotkeys_box = QGroupBox("Клавиши")
        hotkeys_form = QFormLayout(hotkeys_box)
        self.list_key_edit = HotkeyEdit()
        self.start_hotkey_edit = HotkeyEdit()
        self.stop_hotkey_edit = HotkeyEdit()
        self.focus_dota_check = QCheckBox("Перед сбором разворачивать и активировать окно Dota 2")
        self.dota_titles_edit = QLineEdit()
        hotkeys_form.addRow("Кнопка списка игроков", self.list_key_edit)
        hotkeys_form.addRow("Хоткей старта сбора", self.start_hotkey_edit)
        hotkeys_form.addRow("Хоткей остановки", self.stop_hotkey_edit)
        hotkeys_form.addRow("", self.focus_dota_check)
        hotkeys_form.addRow("Заголовки окна Dota", self.dota_titles_edit)
        layout.addWidget(hotkeys_box)

        profile_box = QGroupBox("Steam профили")
        profile_form = QFormLayout(profile_box)
        self.low_level_threshold_spin = QSpinBox()
        self.low_level_threshold_spin.setRange(0, 5000)
        self.low_level_threshold_spin.setSuffix(" lvl")
        self.level_check_delay_spin = self._double_spin(0.0, 60.0)
        self.level_check_delay_spin.setSingleStep(0.5)
        self.level_recheck_hours_spin = QSpinBox()
        self.level_recheck_hours_spin.setRange(0, 24 * 30)
        self.level_recheck_hours_spin.setSuffix(" ч")
        self.check_levels_on_start_check = QCheckBox("Проверять уровни при запуске программы")
        profile_form.addRow("Красная обводка ниже", self.low_level_threshold_spin)
        profile_form.addRow("Пауза между проверками", self.level_check_delay_spin)
        profile_form.addRow("Повторять проверку через", self.level_recheck_hours_spin)
        profile_form.addRow("", self.check_levels_on_start_check)
        layout.addWidget(profile_box)

        delays_box = QGroupBox("Задержки")
        delays_grid = QGridLayout(delays_box)
        self.start_delay_spin = self._double_spin(0.0, 20.0)
        self.after_list_delay_spin = self._double_spin(0.0, 5.0)
        self.release_list_after_slot_click_spin = self._double_spin(0.0, 1.0)
        self.release_list_after_slot_click_spin.setDecimals(3)
        self.release_list_after_slot_click_spin.setSingleStep(0.005)
        self.after_player_click_delay_spin = self._double_spin(0.0, 10.0)
        self.after_drag_delay_spin = self._double_spin(0.0, 5.0)
        self.after_copy_delay_spin = self._double_spin(0.0, 5.0)
        self.clipboard_wait_timeout_spin = self._double_spin(0.1, 5.0)
        self.after_back_delay_spin = self._double_spin(0.0, 5.0)
        self.drag_duration_spin = self._double_spin(0.0, 5.0)
        self._add_labeled(delays_grid, 0, 0, "Перед стартом", self.start_delay_spin)
        self._add_labeled(delays_grid, 0, 2, "После списка", self.after_list_delay_spin)
        self._add_labeled(delays_grid, 0, 4, "Отпуск списка после клика", self.release_list_after_slot_click_spin)
        self._add_labeled(delays_grid, 1, 0, "После клика игрока", self.after_player_click_delay_spin)
        self._add_labeled(delays_grid, 1, 2, "После выделения", self.after_drag_delay_spin)
        self._add_labeled(delays_grid, 2, 0, "После Ctrl+C", self.after_copy_delay_spin)
        self._add_labeled(delays_grid, 2, 2, "Ждать буфер", self.clipboard_wait_timeout_spin)
        self._add_labeled(delays_grid, 3, 0, "После назад", self.after_back_delay_spin)
        self._add_labeled(delays_grid, 3, 2, "Длительность drag", self.drag_duration_spin)
        layout.addWidget(delays_box)

        coords_box = QGroupBox("Координаты игроков")
        coords_grid = QGridLayout(coords_box)
        self.slot_spins: list[tuple[QSpinBox, QSpinBox]] = []
        for idx in range(10):
            label = QLabel(str(idx + 1))
            x_spin = self._coord_spin()
            y_spin = self._coord_spin()
            row = idx // 2
            col = (idx % 2) * 4
            coords_grid.addWidget(label, row, col)
            coords_grid.addWidget(QLabel("X"), row, col + 1)
            coords_grid.addWidget(x_spin, row, col + 2)
            coords_grid.addWidget(QLabel("Y"), row, col + 3)
            coords_grid.addWidget(y_spin, row, col + 4)
            self.slot_spins.append((x_spin, y_spin))
        layout.addWidget(coords_box)

        action_box = QGroupBox("Выделение ID и кнопка назад")
        action_grid = QGridLayout(action_box)
        self.drag_start_x_spin = self._coord_spin()
        self.drag_start_y_spin = self._coord_spin()
        self.drag_end_x_spin = self._coord_spin()
        self.drag_end_y_spin = self._coord_spin()
        self.back_x_spin = self._coord_spin()
        self.back_y_spin = self._coord_spin()
        self._add_labeled(action_grid, 0, 0, "Drag start X", self.drag_start_x_spin)
        self._add_labeled(action_grid, 0, 2, "Drag start Y", self.drag_start_y_spin)
        self._add_labeled(action_grid, 1, 0, "Drag end X", self.drag_end_x_spin)
        self._add_labeled(action_grid, 1, 2, "Drag end Y", self.drag_end_y_spin)
        self._add_labeled(action_grid, 2, 0, "Назад X", self.back_x_spin)
        self._add_labeled(action_grid, 2, 2, "Назад Y", self.back_y_spin)
        layout.addWidget(action_box)

        ocr_box = QGroupBox("OCR")
        ocr_grid = QGridLayout(ocr_box)
        self.ocr_x_spin = self._coord_spin()
        self.ocr_y_spin = self._coord_spin()
        self.ocr_w_spin = self._size_spin()
        self.ocr_h_spin = self._size_spin()
        self.ocr_threshold_spin = QSpinBox()
        self.ocr_threshold_spin.setRange(100, 100)
        self.ocr_threshold_spin.setEnabled(False)
        self.ocr_languages_edit = QLineEdit()
        self.ocr_empty_clipboard_check = QCheckBox("При пустом буфере во время сбора читать ID через OCR")
        self.select_ocr_region_btn = QPushButton("Выделить OCR-область мышью")
        self._add_labeled(ocr_grid, 0, 0, "Область X", self.ocr_x_spin)
        self._add_labeled(ocr_grid, 0, 2, "Область Y", self.ocr_y_spin)
        self._add_labeled(ocr_grid, 1, 0, "Ширина", self.ocr_w_spin)
        self._add_labeled(ocr_grid, 1, 2, "Высота", self.ocr_h_spin)
        self._add_labeled(ocr_grid, 2, 0, "Сходство ID %", self.ocr_threshold_spin)
        self._add_labeled(ocr_grid, 2, 2, "Языки", self.ocr_languages_edit)
        ocr_grid.addWidget(self.ocr_empty_clipboard_check, 3, 0, 1, 4)
        ocr_grid.addWidget(self.select_ocr_region_btn, 4, 0, 1, 4)
        layout.addWidget(ocr_box)
        layout.addStretch(1)
        return tab

    def _wire_signals(self) -> None:
        self.collect_btn.clicked.connect(lambda: self.start_collect("gui"))
        self.stop_btn.clicked.connect(self.stop_collect)
        self.ocr_btn.clicked.connect(self.start_ocr_check)
        self.clipboard_btn.clicked.connect(self.check_clipboard)
        self.select_ocr_region_btn.clicked.connect(self.select_ocr_region)
        self.save_btn.clicked.connect(lambda: self.save_settings(show_message=True))
        self.add_player_btn.clicked.connect(self.add_player)
        self.open_profile_btn.clicked.connect(self.open_selected_steam_profile)
        self.copy_profile_btn.clicked.connect(self.copy_selected_steam_profile_link)
        self.check_levels_btn.clicked.connect(lambda: self.start_profile_level_check("manual"))
        self.delete_player_btn.clicked.connect(self.delete_selected_players)
        self.refresh_players_btn.clicked.connect(self.refresh_players)
        self.add_ignored_btn.clicked.connect(self.add_ignored)
        self.delete_ignored_btn.clicked.connect(self.delete_selected_ignored)
        self.refresh_ignored_btn.clicked.connect(self.refresh_ignored)
        self.players_table.itemChanged.connect(self._player_item_changed)
        self.ignored_table.itemChanged.connect(self._ignored_item_changed)
        self.status_signal.connect(self.set_status)
        self.refresh_signal.connect(self.refresh_all)
        self.collect_state_signal.connect(self.set_collecting)
        self.hotkey_signal.connect(lambda: self.start_collect("hotkey"))
        self.stop_hotkey_signal.connect(self.stop_collect)
        self.clipboard_signal.connect(self.update_last_clipboard)
        self.ocr_match_signal.connect(self._handle_ocr_match)
        self.ocr_done_signal.connect(self._handle_ocr_done)
        self.level_state_signal.connect(self.set_level_checking)
        self.low_level_threshold_spin.valueChanged.connect(lambda _value: self.refresh_players())

    def _load_config_to_widgets(self) -> None:
        cfg = self.config
        self.list_key_edit.setText(str(cfg.get("list_key", "tab")))
        self.start_hotkey_edit.setText(str(cfg.get("start_hotkey", "f8")))
        self.stop_hotkey_edit.setText(str(cfg.get("stop_hotkey", "f9")))
        self.focus_dota_check.setChecked(bool(cfg.get("focus_dota_on_start", True)))
        self.dota_titles_edit.setText(", ".join(cfg.get("dota_window_titles", ["Dota 2", "Dota2"])))
        self.low_level_threshold_spin.setValue(int(cfg.get("low_steam_level_threshold", 5)))
        self.level_check_delay_spin.setValue(float(cfg.get("steam_level_check_delay_sec", 1.2)))
        self.level_recheck_hours_spin.setValue(int(cfg.get("steam_level_recheck_hours", 24)))
        self.check_levels_on_start_check.setChecked(bool(cfg.get("check_levels_on_start", True)))
        self.start_delay_spin.setValue(float(cfg.get("start_delay_sec", 2.0)))
        self.after_list_delay_spin.setValue(float(cfg.get("after_list_delay_sec", 0.35)))
        self.release_list_after_slot_click_spin.setValue(float(cfg.get("release_list_after_slot_click_sec", 0.015)))
        self.after_player_click_delay_spin.setValue(float(cfg.get("after_player_click_delay_sec", 0.8)))
        self.after_drag_delay_spin.setValue(float(cfg.get("after_drag_delay_sec", 0.2)))
        self.after_copy_delay_spin.setValue(float(cfg.get("after_copy_delay_sec", 0.25)))
        self.clipboard_wait_timeout_spin.setValue(float(cfg.get("clipboard_wait_timeout_sec", 1.0)))
        self.after_back_delay_spin.setValue(float(cfg.get("after_back_delay_sec", 0.35)))
        self.drag_duration_spin.setValue(float(cfg.get("drag_duration_sec", 0.25)))

        for idx, slot in enumerate(cfg.get("slots", [])[:10]):
            x_spin, y_spin = self.slot_spins[idx]
            x_spin.setValue(int(slot.get("x", 0)))
            y_spin.setValue(int(slot.get("y", 0)))

        drag = cfg.get("id_drag", {})
        self.drag_start_x_spin.setValue(int(drag.get("start_x", 658)))
        self.drag_start_y_spin.setValue(int(drag.get("start_y", 201)))
        self.drag_end_x_spin.setValue(int(drag.get("end_x", 741)))
        self.drag_end_y_spin.setValue(int(drag.get("end_y", 201)))

        back = cfg.get("back_button", {})
        self.back_x_spin.setValue(int(back.get("x", 32)))
        self.back_y_spin.setValue(int(back.get("y", 34)))

        ocr = cfg.get("ocr_region", {})
        self.ocr_x_spin.setValue(int(ocr.get("x", 0)))
        self.ocr_y_spin.setValue(int(ocr.get("y", 0)))
        self.ocr_w_spin.setValue(int(ocr.get("width", 800)))
        self.ocr_h_spin.setValue(int(ocr.get("height", 400)))
        self.ocr_threshold_spin.setValue(100)
        self.ocr_languages_edit.setText(",".join(cfg.get("ocr_languages", ["ru"])))
        self.ocr_empty_clipboard_check.setChecked(bool(cfg.get("use_ocr_when_clipboard_empty", True)))

    def _config_from_widgets(self) -> dict[str, Any]:
        config = copy.deepcopy(self.config)
        list_key = self._hotkey_value(self.list_key_edit, str(self.config.get("list_key", "tab")))
        config["list_key"] = normalize_key_name(list_key.split("+")[-1])
        config["start_hotkey"] = self._hotkey_value(
            self.start_hotkey_edit,
            str(self.config.get("start_hotkey", "f8")),
        )
        config["stop_hotkey"] = self._hotkey_value(
            self.stop_hotkey_edit,
            str(self.config.get("stop_hotkey", "f9")),
        )
        config["focus_dota_on_start"] = self.focus_dota_check.isChecked()
        config["dota_window_titles"] = [
            title.strip()
            for title in self.dota_titles_edit.text().replace(";", ",").split(",")
            if title.strip()
        ] or ["Dota 2", "Dota2"]
        config["low_steam_level_threshold"] = self.low_level_threshold_spin.value()
        config["steam_level_check_delay_sec"] = self.level_check_delay_spin.value()
        config["steam_level_recheck_hours"] = self.level_recheck_hours_spin.value()
        config["check_levels_on_start"] = self.check_levels_on_start_check.isChecked()
        config["start_delay_sec"] = self.start_delay_spin.value()
        config["after_list_delay_sec"] = self.after_list_delay_spin.value()
        config["release_list_after_slot_click_sec"] = self.release_list_after_slot_click_spin.value()
        config["after_player_click_delay_sec"] = self.after_player_click_delay_spin.value()
        config["after_drag_delay_sec"] = self.after_drag_delay_spin.value()
        config["after_copy_delay_sec"] = self.after_copy_delay_spin.value()
        config["clipboard_wait_timeout_sec"] = self.clipboard_wait_timeout_spin.value()
        config["after_back_delay_sec"] = self.after_back_delay_spin.value()
        config["drag_duration_sec"] = self.drag_duration_spin.value()
        config["slots"] = [
            {"slot": idx + 1, "x": x_spin.value(), "y": y_spin.value()}
            for idx, (x_spin, y_spin) in enumerate(self.slot_spins)
        ]
        config["id_drag"] = {
            "start_x": self.drag_start_x_spin.value(),
            "start_y": self.drag_start_y_spin.value(),
            "end_x": self.drag_end_x_spin.value(),
            "end_y": self.drag_end_y_spin.value(),
        }
        config["back_button"] = {"x": self.back_x_spin.value(), "y": self.back_y_spin.value()}
        config["ocr_region"] = {
            "x": self.ocr_x_spin.value(),
            "y": self.ocr_y_spin.value(),
            "width": self.ocr_w_spin.value(),
            "height": self.ocr_h_spin.value(),
        }
        config["ocr_threshold"] = 100
        config["use_ocr_when_clipboard_empty"] = self.ocr_empty_clipboard_check.isChecked()
        languages = [
            lang.strip().lower()
            for lang in self.ocr_languages_edit.text().replace(";", ",").split(",")
            if lang.strip()
        ]
        config["ocr_languages"] = languages or ["ru"]
        return config

    def save_settings(self, show_message: bool = False, register_hotkey: bool = True) -> None:
        self.config = self._config_from_widgets()
        save_config(self.config, CONFIG_PATH)
        if register_hotkey:
            self._register_hotkey()
        self.set_status("Настройки сохранены.")
        if show_message:
            QMessageBox.information(self, "Настройки", "Настройки сохранены.")

    def refresh_all(self) -> None:
        self.refresh_players()
        self.refresh_ignored()

    def refresh_players(self) -> None:
        self.loading_players = True
        try:
            players = self.db.get_players()
            self.players_table.setRowCount(len(players))
            threshold = self.low_level_threshold_spin.value()
            for row, player in enumerate(players):
                id_item = self._readonly_item(str(player["steam_id_text"]))
                id_item.setData(Qt.UserRole, str(player["id_key"]))
                self.players_table.setItem(row, 0, id_item)
                level = player.get("steam_level")
                level_text = "?" if level is None else str(level)
                self.players_table.setItem(row, 1, self._readonly_item(level_text))
                self.players_table.setItem(row, 2, QTableWidgetItem(str(player.get("note") or "")))
                self.players_table.setItem(row, 3, self._readonly_item(str(player.get("created_at") or "")))
                self.players_table.setItem(row, 4, self._readonly_item(str(player.get("last_seen_at") or "")))
                self.players_table.setItem(row, 5, self._readonly_item(str(player.get("seen_count") or 0)))
                self._mark_low_level_row(row, level, threshold)
        finally:
            self.loading_players = False

    def refresh_ignored(self) -> None:
        self.loading_ignored = True
        try:
            ignored = self.db.get_ignored()
            self.ignored_table.setRowCount(len(ignored))
            for row, item in enumerate(ignored):
                raw_item = self._readonly_item(str(item["raw_text"]))
                raw_item.setData(Qt.UserRole, str(item["id_key"]))
                self.ignored_table.setItem(row, 0, raw_item)
                self.ignored_table.setItem(row, 1, QTableWidgetItem(str(item.get("note") or "")))
                self.ignored_table.setItem(row, 2, self._readonly_item(str(item.get("created_at") or "")))
        finally:
            self.loading_ignored = False

    def add_player(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Добавить ID", "Steam ID / текст:")
        if not ok:
            return
        result = self.db.upsert_player(text)
        self.set_status(self._result_text("Ручное добавление", result))
        self.refresh_players()

    def open_selected_steam_profile(self) -> None:
        raw_id = self._selected_player_text()
        if not raw_id:
            self.set_status("Выбери игрока в таблице.")
            return

        url = steam_profile_url_from_id(raw_id)
        if not url:
            self.set_status("Не удалось собрать ссылку Steam профиля: ID пустой.")
            return

        webbrowser.open(url, new=2)
        self.set_status(f"Открыт Steam профиль: {url}")

    def copy_selected_steam_profile_link(self) -> None:
        raw_id = self._selected_player_text()
        if not raw_id:
            self.set_status("Выбери игрока в таблице.")
            return

        url = steam_profile_url_from_id(raw_id)
        if not url:
            self.set_status("Не удалось собрать ссылку Steam профиля: ID пустой.")
            return

        pyperclip.copy(url)
        self.set_status(f"Ссылка Steam профиля скопирована: {url}")

    def _auto_check_levels_on_start(self) -> None:
        if not bool(self.config.get("check_levels_on_start", True)):
            return
        if not self.db.get_players():
            return
        self.start_profile_level_check("startup")

    def start_profile_level_check(self, source: str = "manual") -> None:
        if self.level_thread and self.level_thread.is_alive():
            self.set_status("Проверка уровней уже выполняется.")
            return
        if self.collect_thread and self.collect_thread.is_alive():
            self.set_status("Сначала останови сбор, потом проверяй уровни.")
            return
        if self.ocr_thread and self.ocr_thread.is_alive():
            self.set_status("Сначала останови OCR, потом проверяй уровни.")
            return

        if source != "startup":
            self.save_settings(show_message=False)

        all_players = self.db.get_players()
        if not all_players:
            self.set_status("В базе пока нет игроков для проверки уровней.")
            return
        players, skipped = self._players_due_for_level_check(all_players)
        if not players:
            self.set_status(
                f"Steam уровни свежие, запросы не отправляю. Пропущено: {skipped}. "
                "Чтобы проверить заново, поставь повтор через 0 ч."
            )
            return

        self.stop_event.clear()
        self.collect_state_signal.emit(True)
        self.level_state_signal.emit(True)
        self.level_thread = threading.Thread(
            target=self._profile_level_worker,
            args=(players, skipped),
            name="steam-level-checker",
            daemon=True,
        )
        self.level_thread.start()
        self.set_status("Проверка Steam уровней запущена.")

    def _profile_level_worker(self, players: list[dict[str, Any]], skipped: int = 0) -> None:
        checked = 0
        low = 0
        unknown = 0
        threshold = int(self.config.get("low_steam_level_threshold", 5))
        delay_sec = max(0.0, float(self.config.get("steam_level_check_delay_sec", 1.2)))

        try:
            for index, player in enumerate(players, start=1):
                if self.stop_event.is_set():
                    break

                steam_id = str(player.get("steam_id_text") or "").strip()
                id_key = str(player.get("id_key") or "").strip()
                self.status_signal.emit(f"Steam level: проверяю {index}/{len(players)} - {steam_id}")

                read_failed = False
                try:
                    level = fetch_steam_profile_level(steam_id)
                except Exception as exc:
                    level = None
                    read_failed = True
                    unknown += 1
                    self.status_signal.emit(
                        f"Steam level: {steam_id}, не удалось прочитать уровень: {self._compact_text(str(exc), 120)}"
                    )

                self.db.update_player_level(id_key, level)
                checked += 1
                if level is None and not read_failed:
                    unknown += 1
                    self.status_signal.emit(f"Steam level: {steam_id}, уровень не найден.")
                elif level is None:
                    pass
                elif level is not None and level < threshold:
                    low += 1
                    self.status_signal.emit(f"Steam level: {steam_id}, уровень {level} ниже порога {threshold}.")
                else:
                    self.status_signal.emit(f"Steam level: {steam_id}, уровень {level}.")

                if checked % 5 == 0 or (level is not None and level < threshold):
                    self.refresh_signal.emit()

                if index < len(players) and delay_sec > 0:
                    self.status_signal.emit(f"Steam level: пауза {delay_sec:.1f} сек, чтобы не словить too many requests.")
                    if not sleep_with_stop(delay_sec, self.stop_event):
                        break

            if self.stop_event.is_set():
                self.status_signal.emit(f"Проверка уровней остановлена. Проверено: {checked}.")
            else:
                self.status_signal.emit(
                    f"Проверка уровней завершена. Проверено: {checked}, низких: {low}, "
                    f"без уровня: {unknown}, свежих пропущено: {skipped}."
                )
        finally:
            self.level_state_signal.emit(False)
            self.collect_state_signal.emit(False)
            self.refresh_signal.emit()

    def _players_due_for_level_check(self, players: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        recheck_hours = int(self.config.get("steam_level_recheck_hours", 24))
        if recheck_hours <= 0:
            return players, 0

        due: list[dict[str, Any]] = []
        skipped = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recheck_hours)
        for player in players:
            checked_at = self._parse_iso_datetime(str(player.get("level_checked_at") or ""))
            if checked_at is None or checked_at < cutoff:
                due.append(player)
            else:
                skipped += 1
        return due, skipped

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def delete_selected_players(self) -> None:
        keys = self._selected_keys(self.players_table)
        if not keys:
            return
        answer = QMessageBox.question(self, "Удалить", f"Удалить выбранных игроков: {len(keys)}?")
        if answer != QMessageBox.Yes:
            return
        for key in keys:
            self.db.delete_player(key)
        self.refresh_players()
        self.set_status("Выбранные игроки удалены.")

    def add_ignored(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Добавить в игнор", "ID / текст:")
        if not ok:
            return
        note, note_ok = QInputDialog.getText(self, "Заметка", "Заметка для игнора:")
        result = self.db.add_ignored(text, note if note_ok else "")
        self.set_status(self._result_text("Игнор", result))
        self.refresh_ignored()

    def delete_selected_ignored(self) -> None:
        keys = self._selected_keys(self.ignored_table)
        if not keys:
            return
        for key in keys:
            self.db.delete_ignored(key)
        self.refresh_ignored()
        self.set_status("Выбранные ID удалены из игнора.")

    def check_clipboard(self) -> None:
        text = pyperclip.paste()
        self.update_last_clipboard(text)
        self.set_status(f"Текущий буфер: {self._compact_text(text)}")

    def update_last_clipboard(self, text: str) -> None:
        self.last_clipboard_edit.setText(self._compact_text(text, limit=140))

    def start_collect(self, source: str) -> None:
        if self.collect_thread and self.collect_thread.is_alive():
            self.set_status("Сбор уже идет.")
            return
        self.save_settings(show_message=False, register_hotkey=(source != "hotkey"))
        self.stop_event.clear()
        self.collect_state_signal.emit(True)
        self.collect_thread = threading.Thread(target=self._collect_worker, name="dota-collector", daemon=True)
        self.collect_thread.start()
        if source == "hotkey":
            self.set_status("Сбор запущен через хоткей.")

    def stop_collect(self) -> None:
        self.stop_event.set()
        self.set_status("Останавливаю текущую операцию...")

    def _collect_worker(self) -> None:
        ocr_service: OcrService | None = None

        def on_status(message: str) -> None:
            self.status_signal.emit(message)

        def on_result(slot_no: int, copied: str, result: dict[str, Any]) -> None:
            self.clipboard_signal.emit(str(result.get("steam_id_text") or copied))
            self.status_signal.emit(self._result_text(f"Слот {slot_no}", result))
            self.refresh_signal.emit()

        def on_empty_clipboard_ocr(slot_no: int) -> str:
            nonlocal ocr_service
            try:
                if ocr_service is None:
                    self.status_signal.emit("Загружаю OCR для fallback...")
                    ocr_service = OcrService(OCR_DIR)
                text = ocr_service.read_digits_region(
                    self.config["ocr_region"],
                    self.config.get("ocr_languages", ["ru"]),
                )
                self.clipboard_signal.emit(f"OCR слот {slot_no}: {text}")
                return text
            except OcrDependencyError as exc:
                self.status_signal.emit(f"Слот {slot_no}: OCR fallback недоступен: {self._compact_text(str(exc), 140)}")
                return ""
            except Exception as exc:
                self.status_signal.emit(f"Слот {slot_no}: OCR fallback ошибка: {self._compact_text(str(exc), 140)}")
                return ""

        try:
            collect_players(
                self.config,
                self.db,
                self.stop_event,
                on_status,
                on_result,
                on_empty_clipboard_ocr,
            )
        except pyautogui.FailSafeException:
            self.status_signal.emit("PyAutoGUI failsafe: мышь уведена в угол экрана, сбор остановлен.")
        except Exception as exc:
            self.status_signal.emit(f"Ошибка сбора: {exc}")
        finally:
            self.collect_state_signal.emit(False)
            self.refresh_signal.emit()

    def start_ocr_check(self) -> None:
        if self.ocr_thread and self.ocr_thread.is_alive():
            self.set_status("OCR уже выполняется.")
            return
        if self.collect_thread and self.collect_thread.is_alive():
            self.set_status("Сначала останови сбор, потом запускай OCR.")
            return
        self.save_settings(show_message=False)
        self.stop_event.clear()
        self.collect_state_signal.emit(True)
        snapshot = copy.deepcopy(self.config)
        self.ocr_thread = threading.Thread(
            target=self._ocr_worker,
            args=(snapshot,),
            name="dota-ocr",
            daemon=True,
        )
        self.ocr_thread.start()
        self.set_status("OCR проверка запущена.")

    def select_ocr_region(self) -> None:
        if self.region_selector is not None:
            return
        self.save_settings(show_message=False)
        self.set_status("Выдели OCR-область мышью. Esc отменяет выбор.")
        self.showMinimized()

        selector = RegionSelector()
        selector.region_selected.connect(self._ocr_region_selected)
        selector.cancelled.connect(self._ocr_region_cancelled)
        selector.destroyed.connect(lambda: setattr(self, "region_selector", None))
        self.region_selector = selector
        selector.show()
        selector.raise_()
        selector.activateWindow()

    def _ocr_region_selected(self, region: dict[str, int]) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.ocr_x_spin.setValue(int(region["x"]))
        self.ocr_y_spin.setValue(int(region["y"]))
        self.ocr_w_spin.setValue(int(region["width"]))
        self.ocr_h_spin.setValue(int(region["height"]))
        self.save_settings(show_message=False)
        self.set_status(
            f"OCR-область сохранена: x={region['x']}, y={region['y']}, "
            f"w={region['width']}, h={region['height']}."
        )

    def _ocr_region_cancelled(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.set_status("Выбор OCR-области отменен.")

    def _ocr_worker(self, config: dict[str, Any]) -> None:
        try:
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.03
            service = OcrService(OCR_DIR)
            players = self.db.get_players()
            all_matches: list[dict[str, Any]] = []
            scanned_texts: list[str] = []
            scanned_slots = 0

            def worker_status(message: str) -> None:
                self.status_signal.emit(message)

            focused = focus_dota_window(config, worker_status)
            if focused:
                worker_status("OCR: Dota 2 активна, жду перед проверкой.")
            else:
                worker_status("OCR: окно Dota 2 не найдено, продолжаю с текущим фокусом.")
            if not sleep_with_stop(float(config.get("start_delay_sec", 2.0)), self.stop_event):
                self.ocr_done_signal.emit(
                    {"text": "", "matches": [], "error": "", "scanned": 0, "stopped": True}
                )
                return

            for index, slot in enumerate(config.get("slots", []), start=1):
                if self.stop_event.is_set():
                    break

                slot_no = int(slot.get("slot", index))
                if not open_player_slot(config, slot, index, self.stop_event, worker_status):
                    break
                if not sleep_with_stop(float(config.get("after_player_click_delay_sec", 0.8)), self.stop_event):
                    break

                worker_status(f"OCR: читаю ID в зоне для слота {slot_no}.")
                text = service.read_digits_region(config["ocr_region"], config.get("ocr_languages", ["ru"]))
                compact_text = self._compact_text(text)
                scanned_texts.append(f"Слот {slot_no}: {compact_text}")
                scanned_slots += 1

                slot_matches = find_exact_digit_matches(text, players)
                if slot_matches:
                    for match in slot_matches:
                        item = dict(match)
                        item["slot"] = slot_no
                        item["ocr_text"] = text
                        all_matches.append(item)
                    self.ocr_match_signal.emit(
                        {"slot": slot_no, "matches": slot_matches, "ocr_text": text}
                    )
                    worker_status(f"OCR: слот {slot_no}, найден ID из базы.")
                else:
                    worker_status(f"OCR: слот {slot_no}, совпадений нет. Текст: {compact_text}")

                back = config.get("back_button", {})
                pyautogui.click(int(back.get("x", 32)), int(back.get("y", 34)))
                if not sleep_with_stop(float(config.get("after_back_delay_sec", 0.35)), self.stop_event):
                    break

            self.ocr_done_signal.emit(
                {
                    "text": "\n".join(scanned_texts),
                    "matches": all_matches,
                    "error": "",
                    "scanned": scanned_slots,
                    "stopped": self.stop_event.is_set(),
                }
            )
        except Exception as exc:
            self.ocr_done_signal.emit({"text": "", "matches": [], "error": str(exc)})

    def _handle_ocr_match(self, payload: dict[str, Any]) -> None:
        slot = payload.get("slot", "?")
        matches = list(payload.get("matches") or [])
        ids = [str(match.get("steam_id_text") or "").strip() for match in matches]
        ids = [steam_id for steam_id in ids if steam_id]
        if not ids:
            return

        QApplication.beep()
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

        visible_ids = ", ".join(ids[:4])
        if len(ids) > 4:
            visible_ids += f" +{len(ids) - 4}"
        title = "Dota Tracker: ID найден"
        message = f"Слот {slot}: {visible_ids} уже есть в базе"

        try:
            if self.toast_notifier is None:
                from win10toast import ToastNotifier

                self.toast_notifier = ToastNotifier()
            self.toast_notifier.show_toast(title, message, duration=5, threaded=True)
        except Exception:
            self.statusBar().showMessage(f"{title} - {message}", 10000)

        self.set_status(message)

    def _handle_ocr_done(self, payload: dict[str, Any]) -> None:
        self.collect_state_signal.emit(False)
        if payload.get("error"):
            self.set_status(f"OCR ошибка: {payload['error']}")
            QMessageBox.warning(self, "OCR ошибка", str(payload["error"]))
            return

        matches = list(payload.get("matches") or [])
        if not matches:
            scanned = int(payload.get("scanned") or 0)
            stopped = bool(payload.get("stopped"))
            if stopped:
                self.set_status(f"OCR остановлен. Проверено слотов: {scanned}. Совпадений не найдено.")
            else:
                self.set_status(f"OCR: проверено слотов: {scanned}, совпадений с базой не найдено.")
            return

        scanned = int(payload.get("scanned") or 0)
        self.set_status(f"OCR завершен: проверено слотов {scanned}, найдено совпадений {len(matches)}.")

    def _register_hotkey(self) -> None:
        start_hotkey = (
            self._hotkey_value(self.start_hotkey_edit, str(self.config.get("start_hotkey", "f8")))
            if hasattr(self, "start_hotkey_edit")
            else "f8"
        )
        stop_hotkey = (
            self._hotkey_value(self.stop_hotkey_edit, str(self.config.get("stop_hotkey", "f9")))
            if hasattr(self, "stop_hotkey_edit")
            else "f9"
        )
        if self.start_hotkey_handle:
            self.start_hotkey_handle.stop()
        if self.stop_hotkey_handle:
            self.stop_hotkey_handle.stop()

        self.start_hotkey_handle = KeyboardHotkey(start_hotkey, lambda: self.hotkey_signal.emit())
        self.start_hotkey_handle.start()

        if stop_hotkey and stop_hotkey != start_hotkey:
            self.stop_hotkey_handle = KeyboardHotkey(stop_hotkey, lambda: self.stop_hotkey_signal.emit())
            self.stop_hotkey_handle.start()
        else:
            self.stop_hotkey_handle = None

    def _player_item_changed(self, item: QTableWidgetItem) -> None:
        if self.loading_players or item.column() != 2:
            return
        key_item = self.players_table.item(item.row(), 0)
        if key_item:
            self.db.update_player_note(str(key_item.data(Qt.UserRole)), item.text())
            self.set_status("Заметка игрока сохранена.")

    def _ignored_item_changed(self, item: QTableWidgetItem) -> None:
        if self.loading_ignored or item.column() != 1:
            return
        key_item = self.ignored_table.item(item.row(), 0)
        if key_item:
            self.db.update_ignored_note(str(key_item.data(Qt.UserRole)), item.text())
            self.set_status("Заметка игнора сохранена.")

    def set_collecting(self, running: bool) -> None:
        self.collect_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def set_level_checking(self, running: bool) -> None:
        self.check_levels_btn.setEnabled(not running)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.statusBar().showMessage(message, 8000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        try:
            self.save_settings(show_message=False, register_hotkey=False)
        except Exception:
            pass
        self.stop_event.set()
        if self.start_hotkey_handle:
            self.start_hotkey_handle.stop()
        if self.stop_hotkey_handle:
            self.stop_hotkey_handle.stop()
        if self.collect_thread and self.collect_thread.is_alive():
            self.collect_thread.join(timeout=1.5)
        if self.level_thread and self.level_thread.is_alive():
            self.level_thread.join(timeout=1.5)
        self.db.close()
        super().closeEvent(event)

    def _selected_keys(self, table: QTableWidget) -> list[str]:
        rows = sorted({index.row() for index in table.selectedIndexes()})
        keys: list[str] = []
        for row in rows:
            item = table.item(row, 0)
            if item:
                keys.append(str(item.data(Qt.UserRole)))
        return keys

    def _selected_player_text(self) -> str:
        rows = sorted({index.row() for index in self.players_table.selectedIndexes()})
        if not rows and self.players_table.currentRow() >= 0:
            rows = [self.players_table.currentRow()]
        if not rows:
            return ""

        item = self.players_table.item(rows[0], 0)
        return str(item.text()).strip() if item else ""

    def _result_text(self, prefix: str, result: dict[str, Any]) -> str:
        status = result.get("status")
        text = str(result.get("steam_id_text") or result.get("raw_text") or "").strip()
        if status == "inserted":
            return f"{prefix}: добавлено {text!r}."
        if status == "updated":
            if "raw_text" in result:
                return f"{prefix}: уже есть, обновлено {text!r}."
            return f"{prefix}: уже есть, счетчик обновлен {text!r}."
        if status == "ignored":
            return f"{prefix}: ID в игноре {text!r}."
        return f"{prefix}: цифры не найдены, ID не записан."

    def _readonly_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _mark_low_level_row(self, row: int, steam_level: Any, threshold: int) -> None:
        is_low = False
        try:
            is_low = steam_level is not None and int(steam_level) < int(threshold)
        except (TypeError, ValueError):
            is_low = False

        background = QColor("#2b0d13") if is_low else QColor("#00000000")
        tooltip = ""
        if is_low:
            tooltip = f"Steam уровень {steam_level} ниже порога {threshold}"

        for col in range(self.players_table.columnCount()):
            item = self.players_table.item(row, col)
            if not item:
                continue
            item.setData(LOW_LEVEL_ROLE, is_low)
            item.setToolTip(tooltip)
            item.setBackground(background)

    def _compact_text(self, value: str, limit: int = 90) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            return "(пусто)"
        if len(text) > limit:
            return text[: limit - 3] + "..."
        return text

    def _hotkey_value(self, edit: QLineEdit, default: str) -> str:
        text = edit.text().strip().lower()
        if not text or "нажми" in text:
            return default.strip().lower()
        return text

    def _coord_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-100000, 100000)
        return spin

    def _size_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 100000)
        return spin

    def _double_spin(self, minimum: float, maximum: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.05)
        return spin

    def _add_labeled(self, layout: QGridLayout, row: int, col: int, label: str, widget: QWidget) -> None:
        layout.addWidget(QLabel(label), row, col)
        layout.addWidget(widget, row, col + 1)


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLE)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
