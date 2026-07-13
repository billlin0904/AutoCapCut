from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QSurfaceFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    ComboBox as QComboBox,
    DoubleSpinBox as QDoubleSpinBox,
    FluentIcon as FIF,
    LineEdit as QLineEdit,
    PlainTextEdit as QPlainTextEdit,
    PrimaryPushButton,
    PushButton as QPushButton,
    SpinBox as QSpinBox,
    TableItemDelegate,
    TableWidget as QTableWidget,
    Theme,
    ToolButton,
    setTheme,
)

from autocapcut_app.core.template_config import TEMPLATE_DIR, load_template_configs, normalize_template_config, template_key
from autocapcut_app.core.preview import extract_preview_frame, render_hook_preview_frame
from autocapcut_app.gui_pyside.workers import (
    PreviewFrameWorker,
    PreviewEngineWorker,
    ShortVideoWorker,
)


DEFAULT_CAPTIONS = [
    {
        "start": 0.2,
        "end": 4.0,
        "segments": [["今天來看", "w"], ["AutoCapCut", "y"]],
        "kind": "main",
    },
    {
        "start": 4.0,
        "end": 8.0,
        "segments": [["把素材變成", "w"], ["直式短影音", "g"]],
        "kind": "main",
    },
]

COLOR_OPTIONS = [
    ("white", "w"),
    ("yellow", "y"),
    ("green", "g"),
    ("red", "r"),
    ("orange", "o"),
]

COLOR_HEX = {
    "w": "#ffffff",
    "y": "#ffd60a",
    "g": "#39e58c",
    "r": "#ff5c5c",
    "o": "#ff9f1c",
}
ASS_PREVIEW_FONT = "Noto Sans TC"
ASS_MAIN_FONT_SIZE = 124
ASS_ADDR_FONT_SIZE = 58
ASS_OUTLINE = 10
LOGO_PIXMAP_CACHE: dict[str, QPixmap] = {}
SCALED_LOGO_PIXMAP_CACHE: dict[tuple[str, int], QPixmap] = {}


def pixel_font(family: str, pixel_size: int, bold: bool = False) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, pixel_size))
    font.setBold(bold)
    return font


def transparent_logo_pixmap(path: Path) -> QPixmap:
    cache_key = str(path.resolve()) if path.exists() else str(path)
    cached = LOGO_PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    image = QImage(str(path))
    if image.isNull():
        return QPixmap()
    if image.hasAlphaChannel():
        pixmap = QPixmap.fromImage(image)
    else:
        image = image.convertToFormat(QImage.Format_RGBA8888)
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                alpha = 255 - max(color.red(), color.green(), color.blue())
                image.setPixelColor(x, y, QColor(255, 255, 255, max(0, min(255, alpha))))
        pixmap = QPixmap.fromImage(image)
    LOGO_PIXMAP_CACHE[cache_key] = pixmap
    return pixmap


def scaled_transparent_logo_pixmap(path: Path, size: int) -> QPixmap:
    cache_key = str(path.resolve()) if path.exists() else str(path)
    size = max(1, int(size))
    scaled_key = (cache_key, size)
    cached = SCALED_LOGO_PIXMAP_CACHE.get(scaled_key)
    if cached is not None:
        return cached
    pixmap = transparent_logo_pixmap(path)
    if pixmap.isNull():
        return pixmap
    scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    SCALED_LOGO_PIXMAP_CACHE[scaled_key] = scaled
    return scaled


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON_PATH)) if APP_ICON_PATH.exists() else QIcon()


def set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoCapCut.AutoCapCut")
    except Exception:
        pass


COLOR_NAME_TO_CODE = {label: code for label, code in COLOR_OPTIONS}
COLOR_NAME_TO_CODE.update({"white": "w", "yellow": "y", "green": "g", "red": "r", "orange": "o"})

KIND_OPTIONS = ["main", "addr"]
UI_FONT_FAMILY = "Segoe UI"
APP_ICON_PATH = Path(__file__).resolve().parents[1] / "autocapcut.ico"
IG_LOGO_PATH = Path(__file__).resolve().parents[1] / "ig-logo.png"
THREADS_LOGO_PATH = Path(__file__).resolve().parents[1] / "threads-logo.png"
CAPTION_STYLE_OPTIONS = [
    ("Manual", "manual"),
    ("Clean", "clean"),
    ("Variety", "variety"),
    ("Pop", "pop"),
]
KARAOKE_OPTIONS = [
    ("Off", "off"),
    ("Active", "active"),
    ("Reveal", "reveal"),
]
CLIP_MOTION_OPTIONS = [
    ("None", "none"),
    ("Ken Burns Zoom", "ken_burns_zoom_in"),
    ("Pan Right", "ken_burns_pan_right"),
    ("Static", "ken_burns_static"),
]
CLIP_GRADE_OPTIONS = [
    ("Inherit", "inherit"),
    ("None", "none"),
    ("Cinematic", "cinematic"),
]
CLIP_TRANSITION_OPTIONS = [
    ("Inherit", "inherit"),
    ("Hard cut", "hard_cut"),
    ("XFade", "xfade"),
]
HOOK_NICHE_OPTIONS = ["teaching", "travel", "food", "finance"]
HOOK_FORMULA_OPTIONS = ["contrarian", "mistake_warning", "list_tease"]
COPYRIGHT_PLATFORM_OPTIONS = [
    ("Instagram", "instagram"),
    ("Threads", "threads"),
]
COPYRIGHT_POSITION_OPTIONS = [
    ("Bottom right", "bottom_right"),
    ("Bottom left", "bottom_left"),
    ("Top right", "top_right"),
    ("Top left", "top_left"),
    ("Bottom center", "bottom_center"),
]

VIDEO_TEMPLATE_OPTIONS = [
    {
        "name": "Basic Subtitle",
        "best_for": "Fast clipping",
        "includes": "Captions, BGM",
        "description": "Clean 9:16 short with source clips, subtitles, and music.",
    },
    {
        "name": "No-face Hook Short",
        "best_for": "Explainer, product, commentary",
        "includes": "Hook card, captions, B-roll structure",
        "description": "Starts with a bold hook card, then uses visuals and captions instead of talking head footage.",
    },
    {
        "name": "Teaching Short",
        "best_for": "Tutorial, knowledge, breakdown",
        "includes": "Hook, key points, emphasis captions",
        "description": "Structured for educational shorts with clear steps, highlighted terms, and readable pacing.",
    },
    {
        "name": "Food/Travel Short",
        "best_for": "Food, travel, silent vlog",
        "includes": "Mood captions, BGM, soft motion",
        "description": "Designed for visual-first clips with music, place/product moments, and simple punchy captions.",
    },
]

CAPTION_TEMPLATES = {
    "Demo": DEFAULT_CAPTIONS,
    "Two Beat Hook": [
        {"start": 0.2, "end": 3.0, "segments": [["先看這個", "y"]], "kind": "main"},
        {"start": 3.0, "end": 7.0, "segments": [["重點是", "w"], ["自動化", "g"]], "kind": "main"},
        {"start": 7.0, "end": 10.0, "segments": [["輸出完成", "w"]], "kind": "addr"},
    ],
    "Clean Address": [
        {"start": 0.2, "end": 4.0, "segments": [["今天來看", "w"], ["重點", "y"]], "kind": "main"},
        {"start": 4.0, "end": 8.0, "segments": [["資訊放這裡", "w"]], "kind": "addr"},
    ],
}


def caption_text_from_segments(segments: list[list[str]]) -> str:
    return "".join(str(segment[0]) for segment in segments if segment)


def caption_color_from_segments(segments: list[list[str]]) -> str:
    fallback = "w"
    for segment in segments:
        if len(segment) > 1 and str(segment[1]).strip():
            color = str(segment[1]).strip()
            if fallback == "w":
                fallback = color
            if color != "w":
                return color
    return fallback


def make_combo(options: list[tuple[str, str]] | list[str], current: str) -> QComboBox:
    combo = QComboBox()
    for opt in options:
        if isinstance(opt, tuple):
            label, value = opt
        else:
            label, value = opt, opt
        combo.addItem(label)
        combo.setItemData(combo.count() - 1, value)
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    return combo


def combo_value(combo: QComboBox | None, fallback: str) -> str:
    if combo is None:
        return fallback
    value = combo.currentData()
    return str(value if value is not None else combo.currentText() or fallback)


class CaptionPreview(QOpenGLWidget):
    def __init__(self) -> None:
        super().__init__()
        fmt = QSurfaceFormat()
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setAutoFillBackground(False)
        self.main_y = 1180
        self.addr_y = 1390
        self.caption_font = ASS_PREVIEW_FONT
        self.main_font_size = ASS_MAIN_FONT_SIZE
        self.addr_font_size = ASS_ADDR_FONT_SIZE
        self.outline = ASS_OUTLINE
        self.shadow = 0
        self.template_name = "Basic Subtitle"
        self.timeline_seconds = 0.0
        self.preview_kind = "main"
        self.show_guides = False
        self.preview_text = "AutoCapCut"
        self.preview_segments: list[list[str]] = [["AutoCapCut", "y"]]
        self.preview_main_segments: list[list[str]] = [["AutoCapCut", "y"]]
        self.preview_addr_segments: list[list[str]] = []
        self.background: QPixmap | None = None
        self.background_has_captions = False
        self.copyright_config: dict = {}
        self.setFixedSize(390, 690)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_positions(self, main_y: int, addr_y: int) -> None:
        self.main_y = main_y
        self.addr_y = addr_y
        self.update()

    def set_caption_style(
        self,
        font: str,
        main_size: int,
        addr_size: int,
        outline: int,
        shadow: int,
    ) -> None:
        self.caption_font = font or ASS_PREVIEW_FONT
        self.main_font_size = max(1, int(main_size or ASS_MAIN_FONT_SIZE))
        self.addr_font_size = max(1, int(addr_size or ASS_ADDR_FONT_SIZE))
        self.outline = max(0, int(outline or 0))
        self.shadow = max(0, int(shadow or 0))
        self.update()

    def set_preview_text(self, text: str) -> None:
        self.preview_text = text or "AutoCapCut"
        self.preview_segments = [[self.preview_text, "y"]]
        self.update()

    def set_preview_segments(self, segments: list[list[str]]) -> None:
        self.preview_segments = segments or []
        if self.preview_kind == "addr":
            self.preview_addr_segments = self.preview_segments
        else:
            self.preview_main_segments = self.preview_segments
        self.preview_text = "".join(str(part[0]) for part in self.preview_segments if part)
        self.update()

    def set_preview_caption_groups(
        self,
        main_segments: list[list[str]] | None,
        addr_segments: list[list[str]] | None,
    ) -> None:
        self.preview_main_segments = main_segments or []
        self.preview_addr_segments = addr_segments or []
        self.preview_segments = self.preview_main_segments or self.preview_addr_segments
        self.preview_text = "".join(str(part[0]) for part in self.preview_segments if part)
        self.update()

    def set_template_name(self, template_name: str) -> None:
        self.template_name = template_name or "Basic Subtitle"
        self.update()

    def set_timeline_seconds(self, seconds: float) -> None:
        self.timeline_seconds = max(0.0, seconds)
        self.update()

    def set_preview_kind(self, kind: str) -> None:
        self.preview_kind = kind if kind in KIND_OPTIONS else "main"
        self.update()

    def set_show_guides(self, enabled: bool) -> None:
        self.show_guides = enabled
        self.update()

    def set_background(self, image_path: str | None, *, has_captions: bool = False) -> None:
        self.background = QPixmap(image_path) if image_path else None
        if self.background is not None and self.background.isNull():
            self.background = None
            has_captions = False
        self.background_has_captions = bool(self.background is not None and has_captions)
        self.update()

    def set_background_image(self, image: QImage | None, *, has_captions: bool = False) -> None:
        self.background = QPixmap.fromImage(image) if image is not None and not image.isNull() else None
        self.background_has_captions = bool(self.background is not None and has_captions)
        self.update()

    def set_copyright_config(self, config: dict | None) -> None:
        self.copyright_config = dict(config or {})
        self.update()

    def paintGL(self) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )

        margin = 10
        available_w = max(1, self.width() - margin * 2)
        available_h = max(1, self.height() - margin * 2)
        scale = min(available_w / 1080, available_h / 1920)
        canvas_w = 1080 * scale
        canvas_h = 1920 * scale
        left = (self.width() - canvas_w) / 2
        top = (self.height() - canvas_h) / 2
        canvas = QRectF(left, top, canvas_w, canvas_h)

        painter.fillRect(self.rect(), QColor("#eeeeef"))
        painter.fillRect(canvas, QColor("#f8fafc"))
        if self.background is not None:
            scaled = self.background.scaled(
                int(canvas_w),
                int(canvas_h),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - int(canvas_w)) // 2)
            sy = max(0, (scaled.height() - int(canvas_h)) // 2)
            painter.drawPixmap(
                int(left),
                int(top),
                scaled.copy(sx, sy, int(canvas_w), int(canvas_h)),
            )
            if not self.background_has_captions:
                painter.fillRect(canvas, QColor(0, 0, 0, 65))

        hook_active = False
        if not self.background_has_captions:
            hook_active = self._draw_template_preview(painter, left, top, canvas_w, canvas_h, scale)
        if self.show_guides:
            self._draw_guides(painter, canvas, left, top, canvas_w, scale)
        if hook_active or self.background_has_captions:
            self._draw_copyright(painter, left, top, canvas_w, canvas_h, scale)
            painter.end()
            return

        self._draw_caption(painter, left, top, canvas_w, scale, self.main_y, self.preview_main_segments, "main")
        self._draw_caption(painter, left, top, canvas_w, scale, self.addr_y, self.preview_addr_segments, "addr")
        self._draw_copyright(painter, left, top, canvas_w, canvas_h, scale)
        painter.end()

    def _draw_template_preview(
        self,
        painter: QPainter,
        left: float,
        top: float,
        width: float,
        height: float,
        scale: float,
    ) -> bool:
        template = self.template_name.lower()
        hook_active = template in {"no-face hook short", "teaching short"} and self.timeline_seconds < 3.0
        if hook_active:
            painter.fillRect(QRectF(left, top, width, height), QColor(10, 18, 28, 95))
            badge = QRectF(left + 56 * scale, top + 120 * scale, 360 * scale, 82 * scale)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#18d0ff"))
            painter.drawRoundedRect(badge, 40 * scale, 40 * scale)
            painter.setPen(QColor("#07111f"))
            painter.setFont(pixel_font(self.caption_font, int(46 * scale), True))
            painter.drawText(badge, Qt.AlignCenter, "你的徽章文字")

            hook_text = self.preview_text[:18] or "今天的重點"
            painter.setFont(pixel_font(self.caption_font, int(104 * scale), True))
            text_rect = QRectF(left + 60 * scale, top + 520 * scale, width - 120 * scale, 260 * scale)
            painter.setPen(QPen(QColor("#000000"), max(2, int(8 * scale))))
            painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, hook_text)
            painter.setPen(QColor("#fff7df"))
            painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, hook_text)
            return True
        elif template == "food/travel short":
            painter.fillRect(QRectF(left, top, width, height), QColor(255, 152, 67, 28))
        return False

    def _draw_guides(
        self,
        painter: QPainter,
        canvas: QRectF,
        left: float,
        top: float,
        width: float,
        scale: float,
    ) -> None:
        top_safe = QRectF(left, top, width, 384 * scale)
        bottom_safe = QRectF(left, top + (1920 - 480) * scale, width, 480 * scale)
        painter.fillRect(top_safe, QColor(255, 204, 77, 35))
        painter.fillRect(bottom_safe, QColor(255, 80, 80, 35))
        painter.setPen(QPen(QColor("#d1d5db"), 1))
        painter.drawRect(canvas)
        painter.setPen(QColor("#94a3b8"))
        painter.drawText(QRectF(left + 8, top + 8, width - 16, 20), "1080 x 1920")
        self._draw_caption_line(painter, left, top, width, scale, self.main_y, "MAIN", [], "#ffd60a")
        self._draw_caption_line(painter, left, top, width, scale, self.addr_y, "ADDR", [], "#ffffff")

    def _draw_caption(
        self,
        painter: QPainter,
        left: float,
        top: float,
        width: float,
        scale: float,
        y_value: int,
        segments: list[list[str]],
        kind: str = "main",
    ) -> None:
        if not segments:
            return
        y = top + y_value * scale
        font_size = self.addr_font_size if kind == "addr" else self.main_font_size
        painter.setFont(pixel_font(self.caption_font, int(font_size * scale), True))
        text_h = max(80 * scale, font_size * scale * 1.4)
        text_rect = QRectF(left + 40 * scale, y - text_h / 2, width - 80 * scale, text_h)
        self._draw_colored_segments(painter, text_rect, segments, scale)

    def _draw_caption_line(
        self,
        painter: QPainter,
        left: float,
        top: float,
        width: float,
        scale: float,
        y_value: int,
        label: str,
        segments: list[list[str]],
        color: str,
    ) -> None:
        y = top + y_value * scale
        painter.setPen(QPen(QColor(color), 2))
        painter.drawLine(int(left), int(y), int(left + width), int(y))
        painter.setFont(QFont(UI_FONT_FAMILY, max(9, int(26 * scale)), QFont.Bold))
        text_rect = QRectF(left + 16, y - 38 * scale, width - 32, 76 * scale)
        self._draw_colored_segments(painter, text_rect, segments, scale)
        painter.setFont(QFont(UI_FONT_FAMILY, max(8, int(15 * scale))))
        painter.setPen(QColor("#cbd5e1"))
        painter.drawText(QRectF(left + 8, y + 5, width - 16, 20), f"{label} y={y_value}")

    def _draw_colored_segments(
        self,
        painter: QPainter,
        rect: QRectF,
        segments: list[list[str]],
        scale: float,
    ) -> None:
        font = painter.font()
        metrics = painter.fontMetrics()
        parts: list[tuple[str, str, int]] = []
        for segment in segments:
            if not segment:
                continue
            text = str(segment[0])
            color_code = str(segment[1]) if len(segment) > 1 else "w"
            width = metrics.horizontalAdvance(text)
            if text and width > 0:
                parts.append((text, color_code, width))
        if not parts:
            return

        total_width = sum(width for _text, _color, width in parts)
        x = rect.left() + max(0.0, (rect.width() - total_width) / 2)
        baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
        outline_width = max(0.0, self.outline * scale)
        shadow_offset = self.shadow * scale
        for text, color_code, part_width in parts:
            path = QPainterPath()
            path.addText(x, baseline, font, text)
            if shadow_offset > 0:
                painter.save()
                painter.translate(shadow_offset, shadow_offset)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 150))
                painter.drawPath(path)
                painter.restore()
            if outline_width > 0:
                painter.setPen(QPen(QColor("#000000"), outline_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLOR_HEX.get(color_code, "#ffffff")))
            painter.drawPath(path)
            x += part_width

    def _draw_copyright(
        self,
        painter: QPainter,
        left: float,
        top: float,
        width: float,
        height: float,
        scale: float,
    ) -> None:
        config = self.copyright_config if isinstance(self.copyright_config, dict) else {}
        if not bool(config.get("enabled", False)):
            return
        account = str(config.get("account", "") or "").strip()
        if not account:
            return
        platform = str(config.get("platform", "instagram") or "instagram").lower()
        logo_path = THREADS_LOGO_PATH if platform == "threads" else IG_LOGO_PATH
        opacity = max(0.05, min(1.0, float(config.get("opacity", 0.85) or 0.85)))
        size = max(0.4, min(2.5, float(config.get("scale", 1.0) or 1.0)))
        logo_px = max(14, int(54 * size * scale))
        logo = scaled_transparent_logo_pixmap(logo_path, logo_px) if logo_path.exists() else QPixmap()
        margin = 42 * scale
        gap = 12 * scale
        y_offset = float(config.get("y_offset", 40) or 0) * scale

        font = QFont(UI_FONT_FAMILY, max(8, int(34 * size * scale)), QFont.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(account)
        text_h = metrics.height()
        block_w = logo_px + gap + text_w
        block_h = max(logo_px, text_h)
        position = str(config.get("position", "bottom_right") or "bottom_right")
        if position == "bottom_left":
            x = left + margin
            y = top + height - margin - block_h
        elif position == "top_right":
            x = left + width - margin - block_w
            y = top + margin
        elif position == "top_left":
            x = left + margin
            y = top + margin
        elif position == "bottom_center":
            x = left + (width - block_w) / 2
            y = top + height - margin - block_h
        else:
            x = left + width - margin - block_w
            y = top + height - margin - block_h
        y += y_offset
        y = max(top, min(top + height - block_h, y))

        painter.save()
        painter.setOpacity(opacity)
        if not logo.isNull():
            painter.drawPixmap(int(x), int(y + (block_h - logo.height()) / 2), logo)
        text_rect = QRectF(x + logo_px + gap, y, text_w + 6 * scale, block_h)
        painter.setPen(QPen(QColor("#000000"), max(1, int(5 * scale))))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, account)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, account)
        painter.restore()


class TimelineSlider(QSlider):
    segmentClicked = Signal(int, int)

    def __init__(self) -> None:
        super().__init__(Qt.Horizontal)
        self.markers: list[tuple[float, float, str]] = []
        self.segments: list[tuple[float, float, int]] = []
        self.setMinimum(0)
        self.setMaximum(1000)
        self.setValue(0)
        self.setFixedHeight(82)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.dark_mode = False
        self.setMouseTracking(True)

    def set_dark_mode(self, enabled: bool) -> None:
        self.dark_mode = bool(enabled)
        self.update()

    def set_markers(self, markers: list[tuple[float, float, str]]) -> None:
        self.markers = markers
        self.update()

    def set_segments(self, segments: list[tuple[float, float] | tuple[float, float, int]]) -> None:
        normalized: list[tuple[float, float, int]] = []
        for index, segment in enumerate(segments):
            if len(segment) < 2:
                continue
            start = float(segment[0])
            end = float(segment[1])
            row = int(segment[2]) if len(segment) > 2 else index
            if end > start:
                normalized.append((start, end, row))
        self.segments = sorted(normalized, key=lambda item: (item[0], item[1], item[2]))
        self.setToolTip(f"{len(self.segments)} caption block(s)")
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            hit = self._segment_at_pos(event.position().x(), event.position().y())
            if hit is not None:
                segment_start, row = hit
                self.setValue(segment_start)
                self.segmentClicked.emit(segment_start, row)
                event.accept()
                return
            self._set_value_from_x(event.position().x())
            self.segmentClicked.emit(self.value(), -1)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._set_value_from_x(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _set_value_from_x(self, x: float) -> None:
        left = 70
        right = self.width() - 12
        span = max(1, right - left)
        ratio = min(1.0, max(0.0, (x - left) / span))
        value = self.minimum() + round(ratio * (self.maximum() - self.minimum()))
        self.setValue(value)

    def _segment_at_pos(self, x: float, y: float) -> tuple[int, int] | None:
        block_y = 42
        block_h = 28
        if not (block_y - 3 <= y <= block_y + block_h + 3):
            return None
        left = 70
        right = self.width() - 12
        span = max(1, right - left)
        max_value = max(1, self.maximum())
        for start_ms, end_ms, row in self.segments:
            raw_x = left + (start_ms / max_value) * span
            raw_end_x = left + (end_ms / max_value) * span
            gap = 6.0
            block_x = raw_x + gap / 2
            block_w = max(5.0, raw_end_x - raw_x - gap)
            if block_x <= x <= block_x + block_w:
                start = int(max(self.minimum(), min(self.maximum(), round(start_ms))))
                return start, row
        return None

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left = 70
        right = self.width() - 12
        span = max(1, right - left)
        max_value = max(1, self.maximum())
        top_y = 8
        block_y = 42
        block_h = 28

        painter.fillRect(self.rect(), QColor(255, 255, 255, 0))
        label_color = QColor("#aab2bf" if self.dark_mode else "#6b7280")
        major_line = QColor("#3b4350" if self.dark_mode else "#d1d5db")
        minor_line = QColor("#2a3039" if self.dark_mode else "#eef0f3")
        empty_block = QColor("#2c3440" if self.dark_mode else "#e5e7eb")
        block_a = QColor("#3c4654" if self.dark_mode else "#c8ced7")
        block_b = QColor("#343d49" if self.dark_mode else "#d8dce3")
        block_text = QColor("#c5ceda" if self.dark_mode else "#64748b")
        block_border = QColor("#20242b" if self.dark_mode else "#ffffff")
        painter.setFont(QFont(UI_FONT_FAMILY, 8))
        painter.setPen(label_color)
        total_sec = max_value / 1000.0
        tick_step = 1
        tick = 0
        while tick <= total_sec + 0.01:
            x = left + (tick * 1000 / max_value) * span
            major = tick % 5 == 0
            if major:
                painter.drawText(QRectF(x - 18, top_y, 36, 14), Qt.AlignCenter, self._tick_label(tick))
            painter.setPen(QPen(major_line if major else minor_line, 1))
            painter.drawLine(int(x), 26 if major else 34, int(x), 76)
            painter.setPen(label_color)
            tick += tick_step

        if self.segments:
            for index, (start_ms, end_ms, row) in enumerate(self.segments):
                raw_x = left + (start_ms / max_value) * span
                raw_end_x = left + (end_ms / max_value) * span
                gap = 6.0
                x = raw_x + gap / 2
                width = max(5.0, raw_end_x - raw_x - gap)
                color = block_b if index % 2 else block_a
                painter.setPen(QPen(block_border, 1))
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, block_y, width, block_h), 7, 7)
                if width >= 14:
                    painter.setPen(block_text)
                    painter.setFont(QFont(UI_FONT_FAMILY, 8))
                    painter.drawText(QRectF(x, block_y + 1, width, block_h - 2), Qt.AlignCenter, str(row + 1))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(empty_block)
            painter.drawRoundedRect(QRectF(left, block_y, span, block_h), 7, 7)

        for start_ms, end_ms, kind in self.markers:
            x = left + (start_ms / max_value) * span
            end_x = left + (end_ms / max_value) * span
            color = QColor("#a855f7") if kind == "main" else QColor("#38bdf8")
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 140), 2))
            if end_ms > start_ms:
                painter.drawLine(int(x), block_y + block_h + 3, int(end_x), block_y + block_h + 3)

        play_x = left + (self.value() / max_value) * span
        painter.setPen(QPen(QColor("#5aa7ff"), 2))
        painter.drawLine(int(play_x), 0, int(play_x), self.height())
        painter.setBrush(QColor("#5aa7ff"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(play_x - 5, 24, 10, 10))

    def _tick_label(self, seconds: float) -> str:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"


class CaptionBlockDelegate(TableItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.dark_mode = False

    def set_dark_mode(self, enabled: bool) -> None:
        self.dark_mode = bool(enabled)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        row = index.row()
        col = index.column()
        model = index.model()
        first = col == 0
        last = col == model.columnCount() - 1
        selected = bool(option.state & QStyle.State_Selected)

        rect = QRectF(option.rect).adjusted(2 if first else 0, 3, -2 if last else 0, -3)
        if first:
            row_rect = QRectF(option.rect)
            for c in range(model.columnCount()):
                idx = model.index(row, c)
                cell_rect = option.widget.visualRect(idx) if option.widget else option.rect
                row_rect = row_rect.united(QRectF(cell_rect))
            row_rect = row_rect.adjusted(4, 3, -4, -3)
            if self.dark_mode:
                border = QColor("#8b5cf6" if selected else "#2b313a")
                fill = QColor("#202838" if selected else "#1a2029")
            else:
                border = QColor("#8b5cf6" if selected else "#ffffff")
                fill = QColor("#eef2ff" if selected else "#f3f4f6")
            painter.setPen(QPen(border, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(row_rect, 6, 6)

        painter.setPen(QColor("#eef2f7" if self.dark_mode else "#111827"))
        painter.setFont(option.font)
        text = index.data() or ""
        align = Qt.AlignVCenter | (Qt.AlignRight if col in (0, 1) else Qt.AlignLeft)
        text_rect = rect.adjusted(8, 0, -8, 0)
        painter.drawText(text_rect, align, str(text))
        painter.restore()


class MainWindow(QMainWindow):
    preview_engine_configure = Signal(object)
    preview_engine_play = Signal(float)
    preview_engine_pause = Signal()
    preview_engine_seek = Signal(float)
    preview_engine_audio_position = Signal(float)
    preview_engine_stop = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCapCut - Short Video MVP")
        self.setWindowIcon(app_icon())
        self.resize(1240, 860)
        self.template_configs = load_template_configs()
        self._updating_template_controls = False
        self._thread: QThread | None = None
        self._worker: ShortVideoWorker | None = None
        self._preview_thread: QThread | None = None
        self._preview_worker: PreviewFrameWorker | None = None
        self._preview_engine_thread: QThread | None = None
        self._preview_engine_worker: PreviewEngineWorker | None = None
        self._preview_playing = False
        self._preview_clip_key: str | None = None
        self._pending_preview_request: tuple[str, float, list[list[str]], str, int, int, str, bool, dict, str, float] | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.load_preview_for_timeline)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self._last_preview_volume = 85
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self.preview_audio_position_changed)
        self._audio_clip_path: str | None = None
        self._preview_audio_master_is_timeline = False
        self._preview_source_audio_key: str | None = None
        self._preview_source_audio_path: Path | None = None
        self.render_settings_dialog: QDialog | None = None
        self.render_log_dialog: QDialog | None = None
        self.render_progress: QProgressBar | None = None
        self.render_progress_label: QLabel | None = None
        self.log_edit: QPlainTextEdit | None = None
        self.last_output: Path | None = None
        self.current_project_path: Path | None = None
        self.current_project_root: Path | None = None
        self.start_preview_engine()
        self._build_ui()

    def _make_scroll_panel(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll.setMinimumSize(0, 0)
        return scroll

    def _build_ui(self) -> None:
        root = QWidget()
        root.setMinimumSize(0, 0)
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self.job_box = self._build_job_box()
        main.addWidget(self._build_top_bar())

        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)

        self.caption_box = self._build_caption_box()
        self.caption_box.setFixedWidth(520)
        workspace.addWidget(self.caption_box)

        stage = QWidget()
        stage.setObjectName("PreviewStage")
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(28, 28, 28, 20)
        stage_layout.addStretch(1)
        preview_stack = QWidget()
        preview_stack.setObjectName("PreviewStack")
        preview_stack.setAttribute(Qt.WA_StyledBackground, False)
        preview_stack_layout = QVBoxLayout(preview_stack)
        preview_stack_layout.setContentsMargins(0, 0, 0, 0)
        preview_stack_layout.setSpacing(2)
        self.preview_box = self._build_preview_box()
        self.preview_box.setFixedSize(390, 690)
        preview_stack_layout.addWidget(self.preview_box, 0, Qt.AlignHCenter)
        preview_stack_layout.addWidget(self._build_preview_controls(), 0, Qt.AlignHCenter)
        stage_layout.addWidget(preview_stack, 0, Qt.AlignHCenter)
        stage_layout.addStretch(1)
        self.preview_scroll = self._make_scroll_panel(stage)
        workspace.addWidget(self.preview_scroll, 1)
        main.addLayout(workspace, 1)

        main.addWidget(self._build_timeline_bar())

        self.apply_saved_theme()
        self.update_caption_preview()
        self.update_timeline_from_inputs()

    def theme_to_key(self, theme: Theme) -> str:
        if theme == Theme.DARK:
            return "dark"
        if theme == Theme.AUTO:
            return "system"
        return "light"

    def theme_from_key(self, key: str) -> Theme:
        normalized = str(key or "light").strip().lower()
        if normalized == "dark":
            return Theme.DARK
        if normalized in {"system", "auto"}:
            return Theme.AUTO
        return Theme.LIGHT

    def app_settings(self) -> QSettings:
        return QSettings("AutoCapCut", "AutoCapCut")

    def last_dialog_dir(self, key: str, fallback: str | Path | None = None) -> str:
        fallback_path = Path(fallback).expanduser() if fallback else Path.home()
        stored = str(self.app_settings().value(f"paths/{key}", "") or "")
        if stored:
            path = Path(stored).expanduser()
            if path.exists():
                return str(path)
        return str(fallback_path)

    def remember_dialog_path(self, key: str, path: str | Path) -> None:
        if not path:
            return
        selected = Path(path).expanduser()
        folder = selected if selected.is_dir() else selected.parent
        if folder:
            self.app_settings().setValue(f"paths/{key}", str(folder))

    def apply_saved_theme(self) -> None:
        theme = self.theme_from_key(str(self.app_settings().value("ui/theme", "light")))
        if hasattr(self, "theme_combo"):
            index = self.theme_combo.findData(theme)
            if index >= 0:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(index)
                self.theme_combo.blockSignals(False)
        self.apply_app_theme(theme)

    def apply_app_theme(self, theme: Theme) -> None:
        setTheme(theme)
        dark = theme == Theme.DARK
        colors = {
            "app_bg": "#111418" if dark else "#f4f4f5",
            "panel_bg": "#181c22" if dark else "#ffffff",
            "stage_bg": "#20242b" if dark else "#eeeeef",
            "text": "#eef2f7" if dark else "#202124",
            "muted": "#aab2bf" if dark else "#5f6368",
            "tab_selected": "#ffffff" if dark else "#111827",
            "border": "#2b313a" if dark else "#e5e7eb",
            "table_bg": "#151920" if dark else "transparent",
        }
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                font-family: "Segoe UI";
                font-size: 10pt;
                background: {colors["app_bg"]};
                color: {colors["text"]};
            }}
            QWidget#TopBar {{
                background: {colors["panel_bg"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QWidget#PreviewStage {{
                background: {colors["stage_bg"]};
            }}
            QWidget#TimelineBar {{
                background: {colors["panel_bg"]};
                border-top: 1px solid {colors["border"]};
            }}
            QWidget#PreviewControls, QWidget#PreviewStack {{
                background: transparent;
            }}
            QLabel#PreviewTimeLabel {{
                background: transparent;
                border: 0px;
                color: {colors["text"]};
            }}
            QSlider {{
                background: transparent;
            }}
            QGroupBox {{
                font-weight: 600;
                font-size: 10pt;
                border: 0px solid transparent;
                margin-top: 12px;
                color: {colors["text"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0px 2px;
            }}
            QPlainTextEdit, QTableWidget, QLineEdit {{
                font-family: "Segoe UI";
                font-size: 10pt;
                color: {colors["text"]};
            }}
            QPushButton {{
                font-family: "Segoe UI";
                min-height: 22px;
                padding: 2px 8px;
                font-size: 10pt;
            }}
            QTabWidget#EditorTabs::pane {{
                border: 0px;
                background: transparent;
            }}
            QTabWidget#EditorTabs QTabBar::tab {{
                background: transparent;
                border: 0px;
                padding: 7px 14px;
                margin-right: 4px;
                color: {colors["muted"]};
            }}
            QTabWidget#EditorTabs QTabBar::tab:selected {{
                color: {colors["tab_selected"]};
                border-bottom: 2px solid #00a6b2;
            }}
            QGroupBox#PreviewBox {{
                background: transparent;
                border: 0px solid transparent;
                margin-top: 0px;
            }}
            QGroupBox#SourceSegmentsBox {{
                border: 0px solid transparent;
                margin-top: 0px;
                padding-top: 0px;
            }}
            QGroupBox#SourceSegmentsBox::title {{
                height: 0px;
                margin: 0px;
                padding: 0px;
            }}
            QTableWidget#CaptionBlockTable {{
                background: {colors["table_bg"]};
                border: 0px;
                selection-background-color: transparent;
                outline: 0px;
            }}
            QTableWidget#CaptionBlockTable::item {{
                border: 0px;
                padding: 0px;
            }}
            ComboBox#CaptionKindCombo {{
                background: transparent;
                border: 0px;
                padding-left: 8px;
                min-height: 28px;
                color: {colors["text"]};
            }}
            QTableWidget#CaptionBlockTable ComboBox {{
                color: {colors["text"]};
            }}
            """
        )
        if hasattr(self, "timeline_slider"):
            self.timeline_slider.set_dark_mode(dark)
        if hasattr(self, "caption_delegate"):
            self.caption_delegate.set_dark_mode(dark)
        if hasattr(self, "caption_table"):
            self.caption_table.viewport().update()
        if hasattr(self, "time_label"):
            self.time_label.setStyleSheet(f"background: transparent; border: 0px; color: {colors['text']};")

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(46)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        new_project_button = QPushButton(FIF.FOLDER_ADD, "New Project")
        new_project_button.clicked.connect(self.new_clip_project)
        import_button = QPushButton(FIF.DOWNLOAD, "Import Segment Plan")
        import_button.clicked.connect(self.import_segment_plan)
        load_project_button = QPushButton(FIF.FOLDER, "Load Project")
        load_project_button.clicked.connect(self.load_project)
        save_project_button = QPushButton(FIF.SAVE, "Save Project")
        save_project_button.clicked.connect(self.save_project)
        settings_button = QPushButton(FIF.SETTING, "Render Settings")
        settings_button.clicked.connect(self.show_render_settings_dialog)
        self.theme_combo = QComboBox()
        self.theme_combo.setFixedWidth(118)
        self.theme_combo.addItem("Light", FIF.PALETTE, Theme.LIGHT)
        self.theme_combo.addItem("Dark", FIF.BRUSH, Theme.DARK)
        self.theme_combo.addItem("System", FIF.PALETTE, Theme.AUTO)
        self.theme_combo.setCurrentIndex(0)
        self.theme_combo.setToolTip("Theme")
        self.theme_combo.currentIndexChanged.connect(self.theme_changed)
        self.open_button = QPushButton(FIF.FOLDER, "Open Folder")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output_folder)
        self.run_button = PrimaryPushButton(FIF.IMAGE_EXPORT, "Export")
        self.run_button.clicked.connect(self.start_render)

        layout.addWidget(new_project_button)
        layout.addWidget(import_button)
        layout.addWidget(load_project_button)
        layout.addWidget(save_project_button)
        layout.addWidget(settings_button)
        layout.addWidget(self.theme_combo)
        layout.addStretch(1)
        layout.addWidget(self.open_button)
        layout.addWidget(self.run_button)
        return bar

    def theme_changed(self, *_args) -> None:
        if not hasattr(self, "theme_combo"):
            return
        theme = self.theme_combo.currentData()
        if theme in (Theme.LIGHT, Theme.DARK, Theme.AUTO):
            self.app_settings().setValue("ui/theme", self.theme_to_key(theme))
            self.apply_app_theme(theme)

    def toggle_preview_guides(self, enabled: bool) -> None:
        if hasattr(self, "preview"):
            self.preview.set_show_guides(enabled)

    def _build_preview_controls(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("PreviewControls")
        panel.setAttribute(Qt.WA_StyledBackground, False)
        panel.setFixedWidth(560)
        panel.setFixedHeight(28)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        panel.setLayout(controls)

        self.to_start_button = ToolButton(FIF.SKIP_BACK)
        self.prev_caption_button = ToolButton(FIF.LEFT_ARROW)
        self.back_button = ToolButton(FIF.PAGE_LEFT)
        self.play_button = ToolButton(FIF.PLAY)
        self.forward_button = ToolButton(FIF.PAGE_RIGHT)
        self.next_caption_button = ToolButton(FIF.RIGHT_ARROW)
        self.to_end_button = ToolButton(FIF.SKIP_FORWARD)
        self.preview_volume_button = ToolButton(FIF.VOLUME)
        self.time_label = QLabel("0.00s / 0.00s")
        self.time_label.setObjectName("PreviewTimeLabel")
        self.time_label.setAttribute(Qt.WA_StyledBackground, False)
        self.time_label.setStyleSheet("background: transparent; border: 0px;")
        self.to_start_button.setToolTip("Jump to start")
        self.prev_caption_button.setToolTip("Previous caption")
        self.back_button.setToolTip("Back 1 second")
        self.play_button.setToolTip("Play")
        self.forward_button.setToolTip("Forward 1 second")
        self.next_caption_button.setToolTip("Next caption")
        self.to_end_button.setToolTip("Jump to end")
        self.preview_volume_button.setToolTip("Mute preview audio")
        self.to_start_button.clicked.connect(self.jump_to_start)
        self.prev_caption_button.clicked.connect(lambda: self.jump_caption(-1))
        self.back_button.clicked.connect(lambda: self.step_timeline(-1.0))
        self.play_button.clicked.connect(self.toggle_playback)
        self.forward_button.clicked.connect(lambda: self.step_timeline(1.0))
        self.next_caption_button.clicked.connect(lambda: self.jump_caption(1))
        self.to_end_button.clicked.connect(self.jump_to_end)
        self.preview_volume_button.clicked.connect(self.toggle_preview_mute)

        self.preview_volume_slider = QSlider(Qt.Horizontal)
        self.preview_volume_slider.setRange(0, 100)
        self.preview_volume_slider.setValue(85)
        self.preview_volume_slider.setFixedWidth(92)
        self.preview_volume_slider.setToolTip("Preview volume")
        self.preview_volume_slider.valueChanged.connect(self.preview_volume_changed)

        controls.addStretch(1)
        controls.addWidget(self.time_label)
        for button in (
            self.to_start_button,
            self.prev_caption_button,
            self.back_button,
            self.play_button,
            self.forward_button,
            self.next_caption_button,
            self.to_end_button,
        ):
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedSize(30, 24)
            controls.addWidget(button)
        self.preview_volume_button.setFocusPolicy(Qt.NoFocus)
        self.preview_volume_button.setFixedSize(30, 24)
        controls.addWidget(self.preview_volume_button)
        controls.addWidget(self.preview_volume_slider)
        controls.addStretch(1)
        return panel

    def _build_timeline_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TimelineBar")
        bar.setFixedHeight(116)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 14)
        layout.setSpacing(8)

        self.timeline_slider = TimelineSlider()
        self.timeline_slider.valueChanged.connect(self.timeline_value_changed)
        self.timeline_slider.segmentClicked.connect(self.timeline_segment_clicked)
        layout.addWidget(self.timeline_slider)
        return bar

    def _build_menu(self) -> None:
        self.file_menu = self.menuBar().addMenu("File")
        import_action = QAction("Import Segment Plan", self)
        import_action.triggered.connect(self.import_segment_plan)
        self.file_menu.addAction(import_action)
        self.file_menu.addSeparator()
        render_settings_action = QAction("Render Settings", self)
        render_settings_action.triggered.connect(self.show_render_settings_dialog)
        self.file_menu.addAction(render_settings_action)

    def _build_clip_box(self) -> QGroupBox:
        box = QGroupBox("")
        box.setObjectName("SourceSegmentsBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(3)
        self.clip_table = QTableWidget()
        self.clip_table.setColumnCount(6)
        self.clip_table.setHorizontalHeaderLabels(["File", "Start Sec", "Duration Sec", "Motion", "Grade", "Transition"])
        self.clip_table.setMinimumHeight(0)
        self.clip_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.clip_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clip_table.setAlternatingRowColors(True)
        self.clip_table.itemChanged.connect(self.schedule_preview_load)
        self.clip_table.itemChanged.connect(self.update_timeline_from_inputs)
        layout.addWidget(self.clip_table, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Add Clip")
        add.clicked.connect(self.add_clip)
        remove = QPushButton("Remove Selected")
        remove.clicked.connect(self.remove_selected_clips)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        self.clip_buttons = QWidget()
        self.clip_buttons.setLayout(buttons)
        self.clip_buttons.hide()
        layout.addWidget(self.clip_buttons)
        return box

    def _build_job_box(self) -> QGroupBox:
        box = QGroupBox("Render Settings")
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(8)

        self.bgm_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.output_edit.setText(str(Path.home() / "Desktop" / "autocapcut_short.mp4"))
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0.0, 2.0)
        self.volume_spin.setSingleStep(0.05)
        self.volume_spin.setValue(0.42)
        self.fade_spin = QDoubleSpinBox()
        self.fade_spin.setRange(0.0, 5.0)
        self.fade_spin.setSingleStep(0.1)
        self.fade_spin.setValue(1.2)
        self.encoder_combo = QComboBox()
        for label, value in (
            ("Auto GPU", "auto"),
            ("CPU libx264", "cpu"),
            ("NVIDIA NVENC", "h264_nvenc"),
            ("Intel Quick Sync", "h264_qsv"),
            ("AMD AMF", "h264_amf"),
        ):
            self.encoder_combo.addItem(label)
            self.encoder_combo.setItemData(self.encoder_combo.count() - 1, value)
        self.quality_combo = QComboBox()
        for label, value in (
            ("Draft Fast", "draft_fast"),
            ("Fast", "fast"),
            ("Balanced", "balanced"),
            ("High Quality", "high"),
        ):
            self.quality_combo.addItem(label)
            self.quality_combo.setItemData(self.quality_combo.count() - 1, value)
        self.set_combo_data(self.quality_combo, "fast")
        self.bgm_start_mode = QComboBox()
        for label, value in (
            ("Auto highlight", "auto"),
            ("Start at 0s", "zero"),
            ("Manual seconds", "manual"),
        ):
            self.bgm_start_mode.addItem(label)
            self.bgm_start_mode.setItemData(self.bgm_start_mode.count() - 1, value)
        self.bgm_start_spin = QDoubleSpinBox()
        self.bgm_start_spin.setRange(0.0, 9999.0)
        self.bgm_start_spin.setSingleStep(0.5)
        self.bgm_start_spin.setValue(0.0)
        self.main_y_spin = QSpinBox()
        self.main_y_spin.setRange(0, 1920)
        self.main_y_spin.setSingleStep(10)
        self.main_y_spin.setValue(1180)
        self.main_y_spin.valueChanged.connect(self.update_caption_preview)
        self.addr_y_spin = QSpinBox()
        self.addr_y_spin.setRange(0, 1920)
        self.addr_y_spin.setSingleStep(10)
        self.addr_y_spin.setValue(1390)
        self.addr_y_spin.valueChanged.connect(self.update_caption_preview)

        form.addRow("Output MP4", self._path_row(self.output_edit, self.pick_output))
        form.addRow("BGM Volume", self.volume_spin)
        form.addRow("Fade Seconds", self.fade_spin)
        form.addRow("Video Encoder", self.encoder_combo)
        form.addRow("Output Quality", self.quality_combo)
        bgm_start_row = QWidget()
        bgm_start_layout = QHBoxLayout(bgm_start_row)
        bgm_start_layout.setContentsMargins(0, 0, 0, 0)
        bgm_start_layout.addWidget(self.bgm_start_mode, 1)
        bgm_start_layout.addWidget(self.bgm_start_spin)
        form.addRow("BGM Start", bgm_start_row)
        form.addRow("Main Caption Y", self.main_y_spin)
        form.addRow("Address Caption Y", self.addr_y_spin)
        return box

    def _build_caption_box(self) -> QGroupBox:
        box = QGroupBox("Caption Editor")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        tabs = QTabWidget()
        tabs.setObjectName("EditorTabs")
        captions_tab = QWidget()
        captions_layout = QVBoxLayout(captions_tab)
        captions_layout.setContentsMargins(0, 6, 0, 0)
        captions_layout.setSpacing(6)

        self.caption_table = QTableWidget()
        self.caption_table.setObjectName("CaptionBlockTable")
        self.caption_table.setColumnCount(8)
        self.caption_table.setMinimumHeight(420)
        self.caption_table.setHorizontalHeaderLabels(["Start", "End", "Kind", "Text", "Color", "Style", "Emphasis", "Karaoke"])
        self.caption_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.caption_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.caption_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.caption_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.caption_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.caption_table.setAlternatingRowColors(False)
        self.caption_table.setShowGrid(False)
        self.caption_table.verticalHeader().setDefaultSectionSize(39)
        self.caption_delegate = CaptionBlockDelegate(self.caption_table)
        self.caption_table.setItemDelegate(self.caption_delegate)
        self.caption_table.itemChanged.connect(self.update_caption_preview)
        self.caption_table.itemChanged.connect(self.update_timeline_from_inputs)
        self.caption_table.itemSelectionChanged.connect(self.caption_selection_changed)
        self.caption_table.cellDoubleClicked.connect(self.jump_to_caption_row)
        captions_layout.addWidget(self.caption_table, 3)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        add = QPushButton("Add")
        add.setToolTip("Add caption")
        add.clicked.connect(self.add_caption_row)
        remove = QPushButton("Remove")
        remove.setToolTip("Remove selected caption")
        remove.clicked.connect(self.remove_selected_captions)
        auto_blocks = QPushButton("Auto")
        auto_blocks.setToolTip("Generate caption blocks from source segments")
        auto_blocks.clicked.connect(self.auto_generate_caption_blocks)
        self.template_combo = QComboBox()
        for name in CAPTION_TEMPLATES:
            self.template_combo.addItem(name)
        template = QPushButton("Load")
        template.setToolTip("Load selected caption template")
        template.clicked.connect(self.load_selected_caption_template)
        for button in (add, remove, auto_blocks, template):
            button.setMinimumWidth(64)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addWidget(auto_blocks)
        buttons.addWidget(self.template_combo)
        buttons.addWidget(template)
        buttons.addStretch(1)
        captions_layout.addLayout(buttons)

        clips_tab = QWidget()
        clips_layout = QVBoxLayout(clips_tab)
        clips_layout.setContentsMargins(0, 6, 0, 0)
        clips_layout.setSpacing(6)
        self.clip_box = self._build_clip_box()
        clips_layout.addWidget(self.clip_box, 1)

        tabs.addTab(self._make_scroll_panel(self._build_video_templates_tab()), "Project")
        tabs.addTab(self._build_copyright_tab(), "Copyright")
        tabs.addTab(captions_tab, "Captions")
        tabs.addTab(clips_tab, "Clips")
        tabs.addTab(self._build_assets_tab(), "Assets")
        layout.addWidget(tabs, 1)
        self.load_template_config_into_controls(self.current_template_config())
        self.load_caption_template("Demo")
        return box

    def _build_copyright_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        form_box = QGroupBox("Copyright")
        form = QFormLayout(form_box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setVerticalSpacing(8)

        self.copyright_enabled = QCheckBox()
        self.copyright_enabled.setText("Enabled")
        self.copyright_platform = make_combo(COPYRIGHT_PLATFORM_OPTIONS, "instagram")
        self.copyright_account = QLineEdit()
        self.copyright_account.setPlaceholderText("@your_account")
        self.copyright_position = make_combo(COPYRIGHT_POSITION_OPTIONS, "bottom_right")
        self.copyright_scale = QDoubleSpinBox()
        self.copyright_scale.setRange(0.4, 2.5)
        self.copyright_scale.setSingleStep(0.1)
        self.copyright_scale.setValue(1.0)
        self.copyright_y_offset = QSpinBox()
        self.copyright_y_offset.setRange(-300, 300)
        self.copyright_y_offset.setSingleStep(5)
        self.copyright_y_offset.setValue(40)
        self.copyright_opacity = QDoubleSpinBox()
        self.copyright_opacity.setRange(0.05, 1.0)
        self.copyright_opacity.setSingleStep(0.05)
        self.copyright_opacity.setValue(0.85)

        form.addRow("Status", self.copyright_enabled)
        form.addRow("Platform", self.copyright_platform)
        form.addRow("Account", self.copyright_account)
        form.addRow("Position", self.copyright_position)
        form.addRow("Size", self.copyright_scale)
        form.addRow("Y Offset", self.copyright_y_offset)
        form.addRow("Opacity", self.copyright_opacity)
        layout.addWidget(form_box)
        layout.addStretch(1)

        for widget in (
            self.copyright_enabled,
            self.copyright_platform,
            self.copyright_account,
            self.copyright_position,
            self.copyright_scale,
            self.copyright_y_offset,
            self.copyright_opacity,
        ):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self.template_config_changed)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.template_config_changed)
            elif isinstance(widget, (QComboBox, QDoubleSpinBox, QSpinBox)):
                widget.currentIndexChanged.connect(self.template_config_changed) if isinstance(widget, QComboBox) else widget.valueChanged.connect(self.template_config_changed)
        return tab

    def _build_assets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        form = QFormLayout()
        self.asset_root_edit = QLineEdit()
        self.bgm_folder_edit = QLineEdit()
        self.broll_folder_edit = QLineEdit()
        self.font_folder_edit = QLineEdit()
        self.bgm_combo = QComboBox()
        self.bgm_combo.addItem("No BGM found")
        self.bgm_combo.setItemData(0, "")
        self.bgm_combo.currentIndexChanged.connect(self.selected_bgm_changed)
        self.asset_root_edit.setPlaceholderText("Use New Project to create a project folder")
        form.addRow("Project Root", self.asset_root_edit)
        form.addRow("BGM Folder", self.bgm_folder_edit)
        form.addRow("Selected BGM", self.bgm_combo)
        form.addRow("B-roll Folder", self.broll_folder_edit)
        form.addRow("Fonts Folder", self.font_folder_edit)
        layout.addLayout(form)

        actions = QHBoxLayout()
        scan_assets = QPushButton("Scan Assets")
        scan_assets.clicked.connect(self.scan_asset_folders)
        match_broll = QPushButton("Match B-roll")
        match_broll.clicked.connect(self.match_broll_to_captions)
        actions.addWidget(scan_assets)
        actions.addWidget(match_broll)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.assets_table = QTableWidget()
        self.assets_table.setColumnCount(4)
        self.assets_table.setHorizontalHeaderLabels(["Type", "File", "Duration", "Path"])
        self.assets_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.assets_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.assets_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.assets_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.assets_table, 1)

        self.broll_match_table = QTableWidget()
        self.broll_match_table.setColumnCount(5)
        self.broll_match_table.setHorizontalHeaderLabels(["Caption", "Suggested B-roll", "Score", "Topic", "Path"])
        self.broll_match_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.broll_match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.broll_match_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.broll_match_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.broll_match_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.broll_match_table, 1)
        return tab

    def _build_video_templates_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)
        picker_row.addWidget(QLabel("Template"))
        self.video_template_combo = QComboBox()
        for template in self.template_configs:
            self.video_template_combo.addItem(template["name"])
        picker_row.addWidget(self.video_template_combo, 1)
        self.video_template_combo.currentIndexChanged.connect(lambda _index: self.apply_video_template_change())
        layout.addLayout(picker_row)

        form_box = QGroupBox("Template Settings")
        form = QFormLayout(form_box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setVerticalSpacing(7)

        self.template_intro_enabled = QCheckBox()
        self.template_intro_enabled.setText("Enabled")
        self.template_intro_type = make_combo([("None", "none"), ("Hook card", "hook_card")], "none")
        self.template_intro_duration = QDoubleSpinBox()
        self.template_intro_duration.setRange(0.0, 10.0)
        self.template_intro_duration.setSingleStep(0.25)
        self.template_hook_niche = make_combo(HOOK_NICHE_OPTIONS, "teaching")
        self.template_hook_formula = make_combo(HOOK_FORMULA_OPTIONS, "contrarian")
        self.template_hook_text = QLineEdit()
        self.template_hook_badge = QLineEdit()

        intro_row = QWidget()
        intro_layout = QHBoxLayout(intro_row)
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.addWidget(self.template_intro_enabled)
        intro_layout.addWidget(self.template_intro_type, 1)
        intro_layout.addWidget(self.template_intro_duration)
        form.addRow("Intro", intro_row)
        form.addRow("Hook Niche", self.template_hook_niche)
        form.addRow("Hook Formula", self.template_hook_formula)
        form.addRow("Hook Text", self.template_hook_text)
        form.addRow("Badge Text", self.template_hook_badge)

        self.template_font_edit = QComboBox()
        self.template_font_edit.addItem("Noto Sans TC")
        self.template_font_edit.setItemData(0, "Noto Sans TC")
        self.template_main_size = QSpinBox()
        self.template_main_size.setRange(20, 220)
        self.template_addr_size = QSpinBox()
        self.template_addr_size.setRange(12, 160)
        self.template_outline = QSpinBox()
        self.template_outline.setRange(0, 40)
        self.template_shadow = QSpinBox()
        self.template_shadow.setRange(0, 30)
        self.template_main_y = QSpinBox()
        self.template_main_y.setRange(0, 1920)
        self.template_main_y.setSingleStep(10)
        self.template_addr_y = QSpinBox()
        self.template_addr_y.setRange(0, 1920)
        self.template_addr_y.setSingleStep(10)
        self.template_color_strategy = make_combo(
            [
                ("Manual", "manual"),
                ("Teaching accent", "teaching_accent"),
                ("Food/Travel accent", "food_travel_accent"),
            ],
            "manual",
        )
        form.addRow("Font", self.template_font_edit)
        form.addRow("Main Size", self.template_main_size)
        form.addRow("Address Size", self.template_addr_size)
        form.addRow("Outline", self.template_outline)
        form.addRow("Shadow", self.template_shadow)
        form.addRow("Main Y", self.template_main_y)
        form.addRow("Address Y", self.template_addr_y)
        form.addRow("Caption Colors", self.template_color_strategy)

        self.template_video_grade = make_combo([("None", "none"), ("Cinematic", "cinematic")], "none")
        self.template_transition = make_combo([("Hard cut", "hard_cut"), ("XFade", "xfade")], "hard_cut")
        self.template_transition_duration = QDoubleSpinBox()
        self.template_transition_duration.setRange(0.1, 3.0)
        self.template_transition_duration.setSingleStep(0.1)
        self.template_preview_fps = QSpinBox()
        self.template_preview_fps.setRange(1, 30)
        form.addRow("Video Grade", self.template_video_grade)
        form.addRow("Transition", self.template_transition)
        form.addRow("Transition Sec", self.template_transition_duration)
        form.addRow("Preview FPS", self.template_preview_fps)

        self.template_bgm_volume = QDoubleSpinBox()
        self.template_bgm_volume.setRange(0.0, 2.0)
        self.template_bgm_volume.setSingleStep(0.05)
        self.template_fade = QDoubleSpinBox()
        self.template_fade.setRange(0.0, 5.0)
        self.template_fade.setSingleStep(0.1)
        self.template_bgm_start = make_combo(
            [("Auto highlight", "auto"), ("Start at 0s", "zero"), ("Manual seconds", "manual")],
            "auto",
        )
        self.template_bgm_start_seconds = QDoubleSpinBox()
        self.template_bgm_start_seconds.setRange(0.0, 9999.0)
        self.template_bgm_start_seconds.setSingleStep(0.5)
        form.addRow("BGM Volume", self.template_bgm_volume)
        form.addRow("Fade Seconds", self.template_fade)
        bgm_start_row = QWidget()
        bgm_start_layout = QHBoxLayout(bgm_start_row)
        bgm_start_layout.setContentsMargins(0, 0, 0, 0)
        bgm_start_layout.addWidget(self.template_bgm_start, 1)
        bgm_start_layout.addWidget(self.template_bgm_start_seconds)
        form.addRow("BGM Start", bgm_start_row)

        for widget in (
            self.template_intro_enabled,
            self.template_intro_type,
            self.template_intro_duration,
            self.template_hook_niche,
            self.template_hook_formula,
            self.template_hook_text,
            self.template_hook_badge,
            self.template_font_edit,
            self.template_main_size,
            self.template_addr_size,
            self.template_outline,
            self.template_shadow,
            self.template_main_y,
            self.template_addr_y,
            self.template_color_strategy,
            self.template_video_grade,
            self.template_transition,
            self.template_transition_duration,
            self.template_preview_fps,
            self.template_bgm_volume,
            self.template_fade,
            self.template_bgm_start,
            self.template_bgm_start_seconds,
        ):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.template_config_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self.template_config_changed)
            elif hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self.template_config_changed)
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(self.template_config_changed)

        action_row = QHBoxLayout()
        save_template = QPushButton("Save Template")
        save_template.clicked.connect(self.save_current_template_config)
        action_row.addWidget(save_template)
        action_row.addStretch(1)
        form.addRow("", action_row)

        layout.addWidget(form_box, 0)
        self.load_template_config_into_controls(self.current_template_config())
        return tab

    def apply_video_template_change(self) -> None:
        if hasattr(self, "template_intro_enabled"):
            self.load_template_config_into_controls(self.current_template_config())
        self.apply_template_side_effects(self.current_template_config())
        self._preview_clip_key = None
        self.stop_preview_playback()
        self.update_timeline_from_inputs()
        self.update_caption_preview()
        self.load_preview_for_timeline()

    def current_template_config(self) -> dict:
        if not hasattr(self, "video_template_combo") or not self.template_configs:
            return normalize_template_config("Basic Subtitle")
        index = self.video_template_combo.currentIndex()
        if 0 <= index < len(self.template_configs):
            return normalize_template_config(str(self.template_configs[index].get("name", "Basic Subtitle")), self.template_configs[index])
        return normalize_template_config("Basic Subtitle")

    def load_template_config_into_controls(self, config: dict) -> None:
        if not hasattr(self, "template_intro_enabled"):
            return
        self._updating_template_controls = True
        try:
            intro = config.get("intro", {})
            caption = config.get("caption", {})
            video = config.get("video", {})
            audio = config.get("audio", {})
            copyright_config = config.get("copyright", {}) if isinstance(config.get("copyright", {}), dict) else {}

            self.template_intro_enabled.setChecked(bool(intro.get("enabled", False)))
            self.set_combo_data(self.template_intro_type, str(intro.get("type", "none")))
            self.template_intro_duration.setValue(float(intro.get("duration", 0.0) or 0.0))
            self.set_combo_data(self.template_hook_niche, str(intro.get("niche", "teaching")))
            self.set_combo_data(self.template_hook_formula, str(intro.get("formula", "contrarian")))
            self.template_hook_text.setText(str(intro.get("hook_text", "")))
            self.template_hook_badge.setText(str(intro.get("badge_text", "")))
            self.set_font_combo_value(str(caption.get("font", "Noto Sans TC")))
            self.template_main_size.setValue(int(caption.get("main_size", 124) or 124))
            self.template_addr_size.setValue(int(caption.get("addr_size", 58) or 58))
            self.template_outline.setValue(int(caption.get("outline", 10) or 0))
            self.template_shadow.setValue(int(caption.get("shadow", 0) or 0))
            self.template_main_y.setValue(int(caption.get("main_y", 1180) or 1180))
            self.template_addr_y.setValue(int(caption.get("addr_y", 1390) or 1390))
            self.set_combo_data(self.template_color_strategy, str(caption.get("color_strategy", "manual")))
            self.set_combo_data(self.template_video_grade, str(video.get("grade", "none")))
            self.set_combo_data(self.template_transition, str(video.get("transition", "hard_cut")))
            self.template_transition_duration.setValue(float(video.get("transition_duration", 0.8) or 0.8))
            self.template_preview_fps.setValue(int(video.get("preview_fps", 12) or 12))
            self.template_bgm_volume.setValue(float(audio.get("volume", 0.42) or 0.42))
            self.template_fade.setValue(float(audio.get("fade", 1.2) or 1.2))
            self.set_combo_data(self.template_bgm_start, str(audio.get("bgm_start", "auto")))
            self.template_bgm_start_seconds.setValue(float(audio.get("bgm_start_seconds", 0.0) or 0.0))
            if hasattr(self, "copyright_enabled"):
                self.copyright_enabled.setChecked(bool(copyright_config.get("enabled", False)))
                self.set_combo_data(self.copyright_platform, str(copyright_config.get("platform", "instagram")))
                self.copyright_account.setText(str(copyright_config.get("account", "")))
                self.set_combo_data(self.copyright_position, str(copyright_config.get("position", "bottom_right")))
                self.copyright_scale.setValue(float(copyright_config.get("scale", 1.0) or 1.0))
                self.copyright_y_offset.setValue(int(copyright_config.get("y_offset", 40) or 0))
                self.copyright_opacity.setValue(float(copyright_config.get("opacity", 0.85) or 0.85))
        finally:
            self._updating_template_controls = False

    def copyright_config_from_controls(self) -> dict:
        if not hasattr(self, "copyright_enabled"):
            return {}
        return {
            "enabled": self.copyright_enabled.isChecked(),
            "platform": combo_value(self.copyright_platform, "instagram"),
            "account": self.copyright_account.text().strip(),
            "position": combo_value(self.copyright_position, "bottom_right"),
            "scale": self.copyright_scale.value(),
            "y_offset": self.copyright_y_offset.value(),
            "opacity": self.copyright_opacity.value(),
            "logos": {
                "instagram": str(IG_LOGO_PATH),
                "threads": str(THREADS_LOGO_PATH),
            },
        }

    def template_config_from_controls(self) -> dict:
        base = self.current_template_config()
        config = dict(base)
        config["intro"] = {
            "enabled": self.template_intro_enabled.isChecked(),
            "duration": self.template_intro_duration.value(),
            "type": combo_value(self.template_intro_type, "none"),
            "niche": combo_value(self.template_hook_niche, "teaching"),
            "formula": combo_value(self.template_hook_formula, "contrarian"),
            "hook_text": self.template_hook_text.text().strip(),
            "badge_text": self.template_hook_badge.text().strip(),
        }
        config["caption"] = {
            "font": self.font_combo_value(),
            "main_size": self.template_main_size.value(),
            "addr_size": self.template_addr_size.value(),
            "outline": self.template_outline.value(),
            "shadow": self.template_shadow.value(),
            "main_y": self.template_main_y.value(),
            "addr_y": self.template_addr_y.value(),
            "color_strategy": combo_value(self.template_color_strategy, "manual"),
        }
        config["video"] = {
            "grade": combo_value(self.template_video_grade, "none"),
            "transition": combo_value(self.template_transition, "hard_cut"),
            "transition_duration": self.template_transition_duration.value(),
            "preview_fps": self.template_preview_fps.value(),
        }
        config["audio"] = {
            "volume": self.template_bgm_volume.value(),
            "fade": self.template_fade.value(),
            "bgm_start": combo_value(self.template_bgm_start, "auto"),
            "bgm_start_seconds": self.template_bgm_start_seconds.value(),
        }
        config["copyright"] = self.copyright_config_from_controls()
        return config

    def template_config_changed(self, *args) -> None:
        del args
        if self._updating_template_controls or not hasattr(self, "video_template_combo"):
            return
        index = self.video_template_combo.currentIndex()
        if 0 <= index < len(self.template_configs):
            self.template_configs[index] = self.template_config_from_controls()
        self.apply_template_side_effects(self.current_template_config())
        self._preview_clip_key = None
        self.update_timeline_from_inputs()
        self.update_caption_preview()
        self.load_preview_for_timeline()

    def apply_template_side_effects(self, config: dict) -> None:
        caption = config.get("caption", {}) if isinstance(config, dict) else {}
        audio = config.get("audio", {}) if isinstance(config, dict) else {}
        if hasattr(self, "main_y_spin") and "main_y" in caption:
            self.main_y_spin.blockSignals(True)
            self.main_y_spin.setValue(int(caption.get("main_y", self.main_y_spin.value()) or self.main_y_spin.value()))
            self.main_y_spin.blockSignals(False)
        if hasattr(self, "addr_y_spin") and "addr_y" in caption:
            self.addr_y_spin.blockSignals(True)
            self.addr_y_spin.setValue(int(caption.get("addr_y", self.addr_y_spin.value()) or self.addr_y_spin.value()))
            self.addr_y_spin.blockSignals(False)
        if hasattr(self, "volume_spin") and "volume" in audio:
            self.volume_spin.setValue(float(audio.get("volume", self.volume_spin.value()) or self.volume_spin.value()))
        if hasattr(self, "fade_spin") and "fade" in audio:
            self.fade_spin.setValue(float(audio.get("fade", self.fade_spin.value()) or self.fade_spin.value()))
        if hasattr(self, "bgm_start_mode") and "bgm_start" in audio:
            value = str(audio.get("bgm_start", "auto"))
            self.set_combo_data(self.bgm_start_mode, value)
            self.bgm_start_spin.setValue(float(audio.get("bgm_start_seconds", 0.0) or 0.0))

    @staticmethod
    def set_combo_data(combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx < 0:
            idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def save_current_template_config(self) -> None:
        index = self.video_template_combo.currentIndex()
        if not (0 <= index < len(self.template_configs)):
            return
        self.template_configs[index] = self.template_config_from_controls()
        config = self.template_configs[index]
        template_id = template_key(str(config.get("id") or config.get("name") or "template"))
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        out = TEMPLATE_DIR / f"{template_id}.json"
        out.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.append_log(f"Saved template: {out}")

    def _build_preview_box(self) -> QGroupBox:
        box = QGroupBox("")
        box.setObjectName("PreviewBox")
        box.setAttribute(Qt.WA_StyledBackground, False)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview = CaptionPreview()
        layout.addWidget(self.preview, 0, Qt.AlignHCenter)
        return box

    def _build_log_box(self) -> QGroupBox:
        box = QGroupBox("Log")
        layout = QVBoxLayout(box)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)
        return box

    def show_render_log_dialog(self) -> None:
        if self.render_log_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Render Log")
            dialog.resize(760, 420)
            layout = QVBoxLayout(dialog)
            self.render_progress_label = QLabel("Ready")
            self.render_progress = QProgressBar()
            self.render_progress.setRange(0, 100)
            self.render_progress.setValue(0)
            self.render_progress.setTextVisible(True)
            layout.addWidget(self.render_progress_label)
            layout.addWidget(self.render_progress)
            self.log_edit = QPlainTextEdit()
            self.log_edit.setReadOnly(True)
            layout.addWidget(self.log_edit)
            self.render_log_dialog = dialog
        self.render_log_dialog.show()
        self.render_log_dialog.raise_()
        self.render_log_dialog.activateWindow()

    def show_render_settings_dialog(self) -> None:
        if self.render_settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Render Settings")
            dialog.resize(520, 340)
            layout = QVBoxLayout(dialog)
            layout.addWidget(self.job_box)
            self.render_settings_dialog = dialog
        self.render_settings_dialog.show()
        self.render_settings_dialog.raise_()
        self.render_settings_dialog.activateWindow()

    def _path_row(self, edit: QLineEdit, callback) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Browse")
        browse.clicked.connect(callback)
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        return row

    def add_caption_row(self, caption: dict | None = None) -> None:
        if not isinstance(caption, dict):
            caption = None
        caption = caption or {
            "start": 0.0,
            "end": 4.0,
            "kind": "main",
            "segments": [["New caption", "w"]],
        }
        row = self.caption_table.rowCount()
        self.caption_table.insertRow(row)
        segments = self.normalize_segments(caption.get("segments", [])) or [["New caption", "w"]]
        start_item = QTableWidgetItem(str(caption.get("start", 0.0)))
        end_item = QTableWidgetItem(str(caption.get("end", 4.0)))
        text_item = QTableWidgetItem(caption_text_from_segments(segments))
        self.caption_table.setItem(row, 0, start_item)
        self.caption_table.setItem(row, 1, end_item)
        self.caption_table.setItem(row, 3, text_item)
        emphasis = caption.get("emphasis", [])
        if isinstance(emphasis, list):
            emphasis_text = ", ".join(str(item) for item in emphasis if str(item).strip())
        else:
            emphasis_text = str(emphasis or "")
        self.caption_table.setItem(row, 6, QTableWidgetItem(emphasis_text))

        kind_combo = make_combo(KIND_OPTIONS, str(caption.get("kind", "main")))
        kind_combo.setObjectName("CaptionKindCombo")
        kind_combo.currentIndexChanged.connect(self.update_caption_preview)
        kind_combo.currentIndexChanged.connect(self.update_timeline_from_inputs)
        self.caption_table.setCellWidget(row, 2, kind_combo)

        color_combo = make_combo(COLOR_OPTIONS, caption_color_from_segments(segments))
        color_combo.setObjectName("CaptionColorCombo")
        color_combo.currentIndexChanged.connect(self.update_caption_preview)
        color_combo.currentIndexChanged.connect(self.update_timeline_from_inputs)
        self.caption_table.setCellWidget(row, 4, color_combo)

        style_combo = make_combo(CAPTION_STYLE_OPTIONS, str(caption.get("style", "manual")))
        style_combo.currentIndexChanged.connect(self.update_caption_preview)
        style_combo.currentIndexChanged.connect(self.update_timeline_from_inputs)
        self.caption_table.setCellWidget(row, 5, style_combo)

        karaoke_combo = make_combo(KARAOKE_OPTIONS, str(caption.get("karaoke", "off")))
        karaoke_combo.currentIndexChanged.connect(self.update_caption_preview)
        karaoke_combo.currentIndexChanged.connect(self.update_timeline_from_inputs)
        self.caption_table.setCellWidget(row, 7, karaoke_combo)

        self.caption_table.selectRow(row)
        self.update_caption_preview()
        self.update_timeline_from_inputs()

    def remove_selected_captions(self) -> None:
        rows = sorted({idx.row() for idx in self.caption_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.caption_table.removeRow(row)
        self.update_caption_preview()
        self.update_timeline_from_inputs()

    def load_sample_captions(self) -> None:
        self.load_caption_template("Demo")

    def load_selected_caption_template(self) -> None:
        self.load_caption_template(self.template_combo.currentText())

    def load_caption_template(self, name: str) -> None:
        captions = CAPTION_TEMPLATES.get(name, DEFAULT_CAPTIONS)
        self.caption_table.setRowCount(0)
        for caption in captions:
            self.add_caption_row(caption)
        if self.caption_table.rowCount():
            self.caption_table.selectRow(0)
        self.update_caption_preview()
        self.update_timeline_from_inputs()

    def auto_generate_caption_blocks(self) -> None:
        total = self.total_timeline_seconds()
        if total <= 0:
            total = 8.0
        self.caption_table.setRowCount(0)
        start = 0.0
        index = 1
        while start < total:
            end = min(total, start + 4.0)
            self.add_caption_row(
                {
                    "start": round(start + 0.2, 2),
                    "end": round(end, 2),
                    "kind": "main",
                    "segments": [[f"Caption {index}", "w"]],
                }
            )
            start = end
            index += 1
        if self.caption_table.rowCount():
            self.caption_table.selectRow(0)
        self.update_caption_preview()
        self.update_timeline_from_inputs()

    def current_caption_row(self) -> int:
        row = self.caption_table.currentRow()
        if 0 <= row < self.caption_table.rowCount():
            return row
        return 0 if self.caption_table.rowCount() else -1

    def normalize_segments(self, segments) -> list[list[str]]:
        valid_colors = {code for _label, code in COLOR_OPTIONS}
        normalized: list[list[str]] = []
        for segment in segments or []:
            if isinstance(segment, dict):
                text = str(segment.get("text", ""))
                color = str(segment.get("color", "w")).strip().lower()
            elif isinstance(segment, (list, tuple)) and segment:
                text = str(segment[0])
                color = str(segment[1]).strip().lower() if len(segment) > 1 else "w"
            else:
                text = str(segment)
                color = "w"
            color = COLOR_NAME_TO_CODE.get(color, color)
            if text:
                normalized.append([text, color if color in valid_colors else "w"])
        return normalized

    def caption_row_segments(self, row: int) -> list[list[str]]:
        text_item = self.caption_table.item(row, 3)
        if text_item is None:
            return []
        text = text_item.text().strip()
        color_combo = self.caption_table.cellWidget(row, 4)
        color = combo_value(color_combo if isinstance(color_combo, QComboBox) else None, "w")
        return self.normalize_segments([[text, color]]) if text else []

    def caption_row_emphasis(self, row: int) -> list[str]:
        item = self.caption_table.item(row, 6)
        if item is None:
            return []
        return [part.strip() for part in re.split(r"[,，\n]", item.text()) if part.strip()]

    def set_caption_row_segments(self, row: int, segments) -> None:
        normalized = self.normalize_segments(segments)
        text_item = self.caption_table.item(row, 3)
        if text_item is None:
            text_item = QTableWidgetItem("")
            self.caption_table.setItem(row, 3, text_item)
        text_item.setText(caption_text_from_segments(normalized))
        color_combo = self.caption_table.cellWidget(row, 4)
        if isinstance(color_combo, QComboBox):
            idx = color_combo.findData(caption_color_from_segments(normalized))
            if idx >= 0:
                color_combo.setCurrentIndex(idx)
        self.update_caption_preview()

    def caption_selection_changed(self) -> None:
        if self.is_preview_playing():
            return
        self.update_caption_preview()

    def jump_to_caption_row(self, row: int, column: int = 0) -> None:
        del column
        if not hasattr(self, "timeline_slider"):
            return
        if row < 0:
            return
        start_item = self.caption_table.item(row, 0)
        if start_item is None:
            return
        try:
            start_ms = int((self.template_intro_seconds() + float(start_item.text())) * 1000)
        except ValueError:
            return
        start_ms = max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), start_ms))
        if self.timeline_slider.value() != start_ms:
            self.timeline_slider.setValue(start_ms)
        self.handle_manual_seek()

    def jump_to_selected_caption(self) -> None:
        self.jump_to_caption_row(self.current_caption_row())

    def caption_rows_with_table_rows(self) -> list[tuple[int, dict]]:
        captions: list[tuple[int, dict]] = []
        for row in range(self.caption_table.rowCount()):
            start_item = self.caption_table.item(row, 0)
            end_item = self.caption_table.item(row, 1)
            kind_combo = self.caption_table.cellWidget(row, 2)
            try:
                start = float(start_item.text()) if start_item and start_item.text().strip() else 0.0
                end = float(end_item.text()) if end_item and end_item.text().strip() else start
            except ValueError:
                continue
            segments = self.caption_row_segments(row)
            if not segments or end <= start:
                continue
            captions.append(
                (
                    row,
                    {
                        "start": start,
                        "end": end,
                        "kind": combo_value(kind_combo if isinstance(kind_combo, QComboBox) else None, "main"),
                        "segments": segments,
                        "style": combo_value(
                            self.caption_table.cellWidget(row, 5)
                            if isinstance(self.caption_table.cellWidget(row, 5), QComboBox)
                            else None,
                            "manual",
                        ),
                        "emphasis": self.caption_row_emphasis(row),
                        "karaoke": combo_value(
                            self.caption_table.cellWidget(row, 7)
                            if isinstance(self.caption_table.cellWidget(row, 7), QComboBox)
                            else None,
                            "off",
                        ),
                    },
                )
            )
        return captions

    def caption_rows(self) -> list[dict]:
        return [caption for _row, caption in self.caption_rows_with_table_rows()]

    def caption_rows_for_render_preview(self) -> list[dict]:
        template_name = self.current_video_template()
        rows: list[dict] = []
        for _order, (table_row, caption) in enumerate(self.caption_rows_with_table_rows()):
            item = dict(caption)
            segments = self.normalize_segments(item.get("segments", []))
            item["segments"] = self.template_preview_segments(template_name, segments, table_row)
            rows.append(item)
        return rows

    def update_caption_preview(self, *args) -> None:
        del args
        if not hasattr(self, "preview") or not hasattr(self, "main_y_spin"):
            return
        if self.is_preview_playing():
            return
        self.preview.set_positions(self.main_y_spin.value(), self.addr_y_spin.value())
        template_name = self.current_video_template()
        template_config = self.current_template_config()
        caption_config = template_config.get("caption", {}) if isinstance(template_config, dict) else {}
        self.preview.set_caption_style(
            str(caption_config.get("font") or "Noto Sans TC"),
            int(caption_config.get("main_size", ASS_MAIN_FONT_SIZE) or ASS_MAIN_FONT_SIZE),
            int(caption_config.get("addr_size", ASS_ADDR_FONT_SIZE) or ASS_ADDR_FONT_SIZE),
            int(caption_config.get("outline", ASS_OUTLINE) or 0),
            int(caption_config.get("shadow", 0) or 0),
        )
        self.preview.set_template_name(template_name)
        self.preview.set_copyright_config(template_config.get("copyright", {}) if isinstance(template_config, dict) else {})
        self.preview.set_timeline_seconds(self.timeline_slider.value() / 1000.0 if hasattr(self, "timeline_slider") else 0.0)
        captions = self.caption_rows_with_table_rows() if hasattr(self, "caption_table") else []
        in_intro = self.rendered_to_source_seconds(self.timeline_slider.value() / 1000.0 if hasattr(self, "timeline_slider") else 0.0) is None
        if in_intro and captions:
            active_row, first = captions[0]
            self.preview.set_preview_kind(str(first.get("kind", "main")))
            segments = self.normalize_segments(first.get("segments", []))
            self.preview.set_preview_caption_groups(
                self.template_preview_segments(template_name, segments, active_row),
                [],
            )
        else:
            groups = self.caption_groups_at_output_time_with_rows(
                captions,
                self.timeline_slider.value() / 1000.0 if hasattr(self, "timeline_slider") else 0.0,
            )
            main_row, main = groups.get("main", (-1, {}))
            addr_row, addr = groups.get("addr", (-1, {}))
            self.preview.set_preview_kind("main" if main else "addr")
            self.preview.set_preview_caption_groups(
                self.template_preview_segments(template_name, self.normalize_segments(main.get("segments", [])), main_row),
                self.template_preview_segments(template_name, self.normalize_segments(addr.get("segments", [])), addr_row),
            )

    def current_video_template(self) -> str:
        if hasattr(self, "video_template_combo"):
            return self.video_template_combo.currentText()
        return "Basic Subtitle"

    def template_intro_seconds(self) -> float:
        intro = self.current_template_config().get("intro", {}) if hasattr(self, "template_configs") else {}
        if bool(intro.get("enabled", False)) and str(intro.get("type", "none")).lower() == "hook_card":
            return max(0.0, float(intro.get("duration", 0.0) or 0.0))
        return 0.0

    def rendered_to_source_seconds(self, timeline_sec: float) -> float | None:
        intro = self.template_intro_seconds()
        if timeline_sec < intro:
            return None
        return max(0.0, timeline_sec - intro)

    def template_preview_segments(self, template_name: str, segments: list[list[str]], row: int) -> list[list[str]]:
        text = "".join(str(part[0]) for part in segments if part).strip()
        if not text:
            return segments
        if row >= 0 and hasattr(self, "caption_table"):
            style_widget = self.caption_table.cellWidget(row, 5)
            style = combo_value(style_widget if isinstance(style_widget, QComboBox) else None, "manual")
            if style != "manual":
                styled = self.caption_style_preview_segments(text, style, self.caption_row_emphasis(row))
                if styled:
                    return styled
        config = normalize_template_config(template_name, self.current_template_config() if hasattr(self, "template_configs") else None)
        strategy = str(config.get("caption", {}).get("color_strategy", "manual")).strip().lower()
        if strategy == "teaching_accent":
            split_at = min(max(2, len(text) // 3), 6)
            accents = ["y", "g", "o"]
            color = accents[(row if row >= 0 else 0) % len(accents)]
            out = [[text[:split_at], color]]
            if text[split_at:]:
                out.append([text[split_at:], "w"])
            return out
        if strategy == "food_travel_accent":
            colors = ["o", "y", "g"]
            index = row if row >= 0 else 0
            return [[text, colors[index % len(colors)]]]
        return segments

    def caption_style_preview_segments(self, text: str, style: str, emphasis: list[str]) -> list[list[str]]:
        try:
            from autocapcut_app.paths import ensure_vendor_on_path

            ensure_vendor_on_path()
            from silent_vlog_maker.shorts_captions import style_caption

            level = {"clean": 1, "variety": 2, "pop": 3}.get(style, 2)
            color_map = {
                "white": "w",
                "cream": "w",
                "gold": "y",
                "yellow": "y",
                "cyan": "g",
                "lime": "g",
                "green": "g",
                "magenta": "r",
                "red": "r",
                "orange": "o",
            }
            return [[str(part), color_map.get(str(color).lower(), "w")] for part, color, _size in style_caption(text, level=level, emphasis=emphasis)]
        except Exception:
            return []

    def caption_at_timeline_time(self, captions: list[dict]) -> dict | None:
        active = self.caption_at_timeline_time_with_row(captions)
        return active[1] if active is not None else None

    def caption_at_timeline_time_with_row(self, captions: list[dict] | list[tuple[int, dict]]) -> tuple[int, dict] | None:
        return self.caption_at_output_time_with_row(captions, self.timeline_slider.value() / 1000.0 if hasattr(self, "timeline_slider") else 0.0)

    def caption_at_output_time_with_row(self, captions: list[dict] | list[tuple[int, dict]], output_time: float) -> tuple[int, dict] | None:
        source_time = self.rendered_to_source_seconds(output_time)
        if source_time is None:
            return None
        for order, item in enumerate(captions):
            if isinstance(item, tuple):
                row, caption = item
            else:
                row, caption = order, item
            try:
                start = float(caption.get("start", 0.0))
                end = float(caption.get("end", start))
            except (TypeError, ValueError):
                continue
            if start <= source_time <= end:
                return row, caption
        return None

    def caption_groups_at_output_time_with_rows(
        self,
        captions: list[dict] | list[tuple[int, dict]],
        output_time: float,
    ) -> dict[str, tuple[int, dict]]:
        source_time = self.rendered_to_source_seconds(output_time)
        if source_time is None:
            return {}
        groups: dict[str, tuple[int, dict]] = {}
        for order, item in enumerate(captions):
            if isinstance(item, tuple):
                row, caption = item
            else:
                row, caption = order, item
            try:
                start = float(caption.get("start", 0.0))
                end = float(caption.get("end", start))
            except (TypeError, ValueError):
                continue
            if not (start <= source_time <= end):
                continue
            kind = str(caption.get("kind", "main"))
            if kind in KIND_OPTIONS and kind not in groups:
                groups[kind] = (row, caption)
        return groups

    def clip_rows(self) -> list[dict]:
        rows = []
        for row in range(self.clip_table.rowCount()):
            file_item = self.clip_table.item(row, 0)
            start_item = self.clip_table.item(row, 1)
            dur_item = self.clip_table.item(row, 2)
            if not file_item or not file_item.text().strip():
                continue
            try:
                start = float(start_item.text()) if start_item and start_item.text().strip() else 0.0
            except ValueError:
                start = 0.0
            try:
                duration = float(dur_item.text()) if dur_item and dur_item.text().strip() else 0.0
            except ValueError:
                duration = 0.0
            if duration > 0:
                rows.append(
                    {
                        "path": file_item.text().strip(),
                        "start": start,
                        "duration": duration,
                        "motion": combo_value(
                            self.clip_table.cellWidget(row, 3)
                            if isinstance(self.clip_table.cellWidget(row, 3), QComboBox)
                            else None,
                            "none",
                        ),
                        "grade": combo_value(
                            self.clip_table.cellWidget(row, 4)
                            if isinstance(self.clip_table.cellWidget(row, 4), QComboBox)
                            else None,
                            "inherit",
                        ),
                        "transition": combo_value(
                            self.clip_table.cellWidget(row, 5)
                            if isinstance(self.clip_table.cellWidget(row, 5), QComboBox)
                            else None,
                            "inherit",
                        ),
                    }
                )
        return rows

    def set_clip_effect_widgets(self, row: int, clip: dict | None = None) -> None:
        clip = clip or {}
        motion = make_combo(CLIP_MOTION_OPTIONS, str(clip.get("motion", "none")))
        grade = make_combo(CLIP_GRADE_OPTIONS, str(clip.get("grade", "inherit")))
        transition = make_combo(CLIP_TRANSITION_OPTIONS, str(clip.get("transition", "inherit")))
        for combo in (motion, grade, transition):
            combo.currentIndexChanged.connect(self.update_timeline_from_inputs)
            combo.currentIndexChanged.connect(self.schedule_preview_load)
        self.clip_table.setCellWidget(row, 3, motion)
        self.clip_table.setCellWidget(row, 4, grade)
        self.clip_table.setCellWidget(row, 5, transition)

    def total_timeline_seconds(self) -> float:
        return self.template_intro_seconds() + sum(row["duration"] for row in self.clip_rows())

    def update_timeline_from_inputs(self, *args) -> None:
        del args
        if not hasattr(self, "timeline_slider"):
            return
        total_ms = max(1, int(self.total_timeline_seconds() * 1000))
        old_value = self.timeline_slider.value()
        self.timeline_slider.setMaximum(total_ms)
        self.timeline_slider.setValue(min(old_value, total_ms))

        caption_blocks: list[tuple[float, float, int]] = []
        intro_ms = self.template_intro_seconds() * 1000
        for row, cap in self.caption_rows_with_table_rows():
            try:
                start_sec = max(0.0, float(cap.get("start", 0)))
                end_sec = max(start_sec, float(cap.get("end", start_sec)))
                start_ms = intro_ms + start_sec * 1000
                end_ms = intro_ms + end_sec * 1000
                caption_blocks.append((min(start_ms, total_ms), min(end_ms, total_ms), row))
            except Exception:
                continue
        self.timeline_slider.set_segments(caption_blocks)
        self.timeline_slider.set_markers([])
        self.update_time_label()

    def timeline_value_changed(self, value: int) -> None:
        del value
        self.update_time_label()
        self.update_caption_preview()
        if self.is_preview_playing():
            return
        self._preview_timer.start(80)

    def timeline_segment_clicked(self, start_ms: int, row: int) -> None:
        del start_ms
        if 0 <= row < self.caption_table.rowCount():
            self.caption_table.blockSignals(True)
            self.caption_table.selectRow(row)
            self.caption_table.blockSignals(False)
            self.update_caption_preview()
        self.handle_manual_seek()

    def jump_to_start(self) -> None:
        self.timeline_slider.setValue(0)
        self.handle_manual_seek()

    def jump_to_end(self) -> None:
        self.timeline_slider.setValue(self.timeline_slider.maximum())
        self.handle_manual_seek()

    def step_timeline(self, seconds: float, sync_audio: bool = True, refresh_preview: bool = True) -> None:
        next_value = self.timeline_slider.value() + int(seconds * 1000)
        next_value = max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), next_value))
        self.timeline_slider.setValue(next_value)
        if refresh_preview:
            self.handle_manual_seek()
        elif sync_audio and self.is_preview_playing():
            self.sync_preview_audio(force=True)

    def start_preview_engine(self) -> None:
        if self._preview_engine_thread is not None:
            return
        thread = QThread(self)
        worker = PreviewEngineWorker()
        self._preview_engine_thread = thread
        self._preview_engine_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        self.preview_engine_configure.connect(worker.configure)
        self.preview_engine_play.connect(worker.play)
        self.preview_engine_pause.connect(worker.pause)
        self.preview_engine_seek.connect(worker.seek)
        self.preview_engine_audio_position.connect(worker.set_audio_timeline)
        self.preview_engine_stop.connect(worker.stop)
        worker.frame_ready.connect(self.preview_playback_frame_ready)
        worker.time_ready.connect(self.preview_engine_time_ready)
        worker.finished.connect(self.preview_engine_finished)
        worker.failed.connect(self.pyav_playback_failed)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda thread=thread: self._clear_preview_engine_thread(thread))
        thread.start()

    def configure_preview_engine(self) -> None:
        template_config = self.current_template_config()
        intro = template_config.get("intro", {}) if isinstance(template_config, dict) else {}
        hook_title = ""
        captions = self.caption_rows_for_render_preview()
        if captions:
            hook_title = caption_text_from_segments(self.normalize_segments(captions[0].get("segments", [])))
        config = {
            "clips": self.clip_rows(),
            "captions": captions,
            "intro_seconds": self.template_intro_seconds(),
            "intro": dict(intro) if isinstance(intro, dict) else {},
            "hook_title": hook_title,
            "duration": self.total_timeline_seconds(),
            "main_y": self.main_y_spin.value() if hasattr(self, "main_y_spin") else 1180,
            "addr_y": self.addr_y_spin.value() if hasattr(self, "addr_y_spin") else 1390,
            "video_template": self.current_video_template(),
            "template_config": template_config,
            "font_dir": self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "",
            "fps": float(template_config.get("video", {}).get("preview_fps", 12) or 12)
            if isinstance(template_config, dict)
            else 12.0,
        }
        self.preview_engine_configure.emit(config)

    def preview_audio_position_changed(self, position_ms: int) -> None:
        if self.is_preview_playing() and self._preview_audio_master_is_timeline:
            self.preview_engine_audio_position.emit(max(0.0, position_ms / 1000.0))

    def preview_engine_time_ready(self, timeline_sec: float) -> None:
        if not self.is_preview_playing():
            return
        timeline_ms = int(max(0.0, timeline_sec) * 1000)
        timeline_ms = max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), timeline_ms))
        if self.timeline_slider.value() != timeline_ms:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(timeline_ms)
            self.timeline_slider.blockSignals(False)
            self.update_time_label()
        self.update_playback_caption_overlay(timeline_sec)

    def preview_engine_finished(self) -> None:
        self.media_player.pause()
        self._preview_playing = False
        self.set_play_button_state(False)
        self.load_preview_for_timeline()

    def _clear_preview_engine_thread(self, thread: QThread) -> None:
        if self._preview_engine_thread is thread:
            self._preview_engine_thread = None
            self._preview_engine_worker = None

    def handle_manual_seek(self) -> None:
        self._preview_clip_key = None
        timeline_sec = self.timeline_slider.value() / 1000.0 if hasattr(self, "timeline_slider") else 0.0
        if self.is_preview_playing():
            self.sync_preview_audio(force=True)
            self.configure_preview_engine()
            self.preview_engine_seek.emit(timeline_sec)
        else:
            self.load_preview_for_timeline()

    def toggle_playback(self) -> None:
        if self.is_preview_playing():
            self.stop_preview_playback()
            self.set_play_button_state(False)
        else:
            self._preview_playing = True
            self.configure_preview_engine()
            self.sync_preview_audio(force=True)
            self.preview_engine_play.emit(self.timeline_slider.value() / 1000.0)
            self.set_play_button_state(True)

    def is_preview_playing(self) -> bool:
        return self._preview_playing

    def stop_preview_playback(self) -> None:
        self._preview_playing = False
        self.preview_engine_pause.emit()
        self.media_player.pause()
        self.set_play_button_state(False)

    def preview_playback_frame_ready(self, image: QImage, source_seconds: float) -> None:
        if not self.is_preview_playing():
            return
        timeline_ms = int(source_seconds * 1000)
        timeline_ms = max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), timeline_ms))
        if self.timeline_slider.value() != timeline_ms:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(timeline_ms)
            self.timeline_slider.blockSignals(False)
            self.update_time_label()
        in_intro = self.rendered_to_source_seconds(source_seconds) is None
        self.update_playback_caption_overlay(source_seconds)
        self.preview.set_background_image(image, has_captions=in_intro)
        if timeline_ms >= self.timeline_slider.maximum():
            self.preview_engine_pause.emit()
            self.media_player.pause()
            self._preview_playing = False
            self.set_play_button_state(False)
            self.load_preview_for_timeline()

    def update_playback_caption_overlay(self, timeline_sec: float) -> None:
        if not hasattr(self, "preview"):
            return
        if hasattr(self, "main_y_spin"):
            self.preview.set_positions(self.main_y_spin.value(), self.addr_y_spin.value())
        template_name = self.current_video_template()
        template_config = self.current_template_config()
        caption_config = template_config.get("caption", {}) if isinstance(template_config, dict) else {}
        self.preview.set_caption_style(
            str(caption_config.get("font") or "Noto Sans TC"),
            int(caption_config.get("main_size", ASS_MAIN_FONT_SIZE) or ASS_MAIN_FONT_SIZE),
            int(caption_config.get("addr_size", ASS_ADDR_FONT_SIZE) or ASS_ADDR_FONT_SIZE),
            int(caption_config.get("outline", ASS_OUTLINE) or 0),
            int(caption_config.get("shadow", 0) or 0),
        )
        self.preview.set_template_name(template_name)
        self.preview.set_copyright_config(template_config.get("copyright", {}) if isinstance(template_config, dict) else {})
        self.preview.set_timeline_seconds(timeline_sec)
        captions = self.caption_rows_with_table_rows() if hasattr(self, "caption_table") else []
        groups = self.caption_groups_at_output_time_with_rows(captions, timeline_sec)
        main_row, main = groups.get("main", (-1, {}))
        addr_row, addr = groups.get("addr", (-1, {}))
        self.preview.set_preview_kind("main" if main else "addr")
        self.preview.set_preview_caption_groups(
            self.template_preview_segments(template_name, self.normalize_segments(main.get("segments", [])), main_row),
            self.template_preview_segments(template_name, self.normalize_segments(addr.get("segments", [])), addr_row),
        )

    def pyav_playback_failed(self, message: str) -> None:
        self.append_log("PreviewEngine playback failed")
        self.append_log(message.splitlines()[0] if message else "Unknown PyAV playback error")
        self.stop_preview_playback()

    def set_play_button_state(self, playing: bool) -> None:
        self.play_button.setIcon(FIF.PAUSE if playing else FIF.PLAY)
        self.play_button.setToolTip("Pause" if playing else "Play")

    def preview_volume_changed(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.audio_output.setVolume(value / 100.0)
        if value > 0:
            self._last_preview_volume = value
        if hasattr(self, "preview_volume_button"):
            self.preview_volume_button.setIcon(FIF.MUTE if value <= 0 else FIF.VOLUME)
            self.preview_volume_button.setToolTip("Unmute preview audio" if value <= 0 else "Mute preview audio")

    def toggle_preview_mute(self) -> None:
        if not hasattr(self, "preview_volume_slider"):
            return
        if self.preview_volume_slider.value() > 0:
            self._last_preview_volume = self.preview_volume_slider.value()
            self.preview_volume_slider.setValue(0)
        else:
            self.preview_volume_slider.setValue(max(1, self._last_preview_volume))

    def source_preview_audio_cache_dir(self) -> Path:
        root = self.current_project_root if self.current_project_root else Path(tempfile.gettempdir()) / "AutoCapCut"
        cache = root / ".preview_audio"
        cache.mkdir(parents=True, exist_ok=True)
        return cache

    def clip_has_audio(self, path: Path) -> bool:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=index",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except Exception:
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def preview_source_audio_key(self) -> str:
        items: list[dict] = []
        for clip in self.clip_rows():
            path = Path(str(clip.get("path", ""))).expanduser()
            stat_key = ""
            try:
                stat = path.stat()
                stat_key = f"{stat.st_mtime_ns}:{stat.st_size}"
            except OSError:
                pass
            items.append(
                {
                    "path": str(path),
                    "stat": stat_key,
                    "start": round(float(clip.get("start", 0.0)), 3),
                    "duration": round(float(clip.get("duration", 0.0)), 3),
                }
            )
        payload = {
            "intro": round(self.template_intro_seconds(), 3),
            "clips": items,
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def preview_source_audio_path(self) -> Path | None:
        clips = self.clip_rows()
        if not clips:
            return None
        key = self.preview_source_audio_key()
        cached = self.source_preview_audio_cache_dir() / f"source_timeline_{key}.m4a"
        if self._preview_source_audio_key == key and self._preview_source_audio_path == cached and cached.exists():
            return cached
        if cached.exists():
            self._preview_source_audio_key = key
            self._preview_source_audio_path = cached
            return cached

        cmd = ["ffmpeg", "-v", "error", "-y"]
        chains: list[str] = []
        labels: list[str] = []

        def add_silence(duration: float) -> None:
            if duration <= 0:
                return
            input_index = len(labels)
            label = f"a{input_index}"
            cmd.extend(["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
            chains.append(f"[{input_index}:a]asetpts=PTS-STARTPTS,aresample=48000[{label}]")
            labels.append(f"[{label}]")

        add_silence(self.template_intro_seconds())
        for clip in clips:
            duration = max(0.05, float(clip.get("duration", 0.0)))
            input_index = len(labels)
            label = f"a{input_index}"
            path = Path(str(clip.get("path", ""))).expanduser()
            if path.exists() and self.clip_has_audio(path):
                start = max(0.0, float(clip.get("start", 0.0)))
                cmd.extend(["-i", str(path)])
                chains.append(
                    f"[{input_index}:a]atrim=start={start:.3f}:duration={duration:.3f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[{label}]"
                )
            else:
                cmd.extend(["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
                chains.append(f"[{input_index}:a]asetpts=PTS-STARTPTS,aresample=48000[{label}]")
            labels.append(f"[{label}]")

        if not labels:
            return None
        if len(labels) == 1:
            filter_complex = ";".join(chains) + f";{labels[0]}anull[aout]"
        else:
            filter_complex = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]"

        self.append_log("Building preview audio from source clips...")
        result = subprocess.run(
            cmd
            + [
                "-filter_complex",
                filter_complex,
                "-map",
                "[aout]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(cached),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            self.append_log((result.stderr or result.stdout or "Preview audio build failed")[-500:])
            return None
        self._preview_source_audio_key = key
        self._preview_source_audio_path = cached
        return cached

    def sync_preview_audio(self, force: bool = False) -> None:
        if self.sync_preview_bgm(force=force):
            self._preview_audio_master_is_timeline = False
            return
        path = self.preview_source_audio_path()
        if path is None or not path.exists():
            self._preview_audio_master_is_timeline = False
            self.media_player.pause()
            return
        source_key = f"source-timeline|{path}"
        if self._audio_clip_path != source_key:
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._audio_clip_path = source_key
            force = True
        self._preview_audio_master_is_timeline = True
        target_ms = max(0, self.timeline_slider.value() if hasattr(self, "timeline_slider") else 0)
        if force or abs(self.media_player.position() - target_ms) > 500:
            self.media_player.setPosition(target_ms)
        self.media_player.play()

    def sync_preview_bgm(self, force: bool = False) -> bool:
        if not hasattr(self, "bgm_edit"):
            return False
        bgm_path = self.bgm_edit.text().strip()
        if not bgm_path:
            return False
        path = Path(bgm_path)
        if not path.exists():
            return False
        self._preview_audio_master_is_timeline = False
        source_key = f"bgm|{path}"
        if self._audio_clip_path != source_key:
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._audio_clip_path = source_key
            force = True
        target_ms = self.preview_bgm_position_ms()
        duration = self.media_player.duration()
        if duration > 0:
            target_ms %= duration
        if force or abs(self.media_player.position() - target_ms) > 900:
            self.media_player.setPosition(target_ms)
        self.media_player.play()
        return True

    def preview_bgm_position_ms(self) -> int:
        timeline_ms = self.timeline_slider.value() if hasattr(self, "timeline_slider") else 0
        offset = 0.0
        if hasattr(self, "bgm_start_mode"):
            mode = combo_value(self.bgm_start_mode, "auto")
            if mode == "manual":
                offset = self.bgm_start_spin.value()
            elif mode == "zero":
                offset = 0.0
        return max(0, int(offset * 1000) + timeline_ms)

    def caption_markers_ms(self) -> list[int]:
        values = sorted({int(start) for start, *_rest in self.timeline_slider.segments})
        return values

    def jump_caption(self, direction: int) -> None:
        markers = self.caption_markers_ms()
        if not markers:
            return
        current = self.timeline_slider.value()
        if direction > 0:
            target = next((m for m in markers if m > current + 25), markers[-1])
        else:
            prev = [m for m in markers if m < current - 25]
            target = prev[-1] if prev else markers[0]
        self.timeline_slider.setValue(max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), target)))
        self.handle_manual_seek()

    def update_time_label(self) -> None:
        if not hasattr(self, "timeline_slider"):
            return
        cur = self.timeline_slider.value() / 1000.0
        total = self.timeline_slider.maximum() / 1000.0
        self.time_label.setText(f"{cur:.2f}s / {total:.2f}s")

    def clip_at_timeline_time(self, timeline_sec: float) -> tuple[str, float] | None:
        detail = self.clip_detail_at_timeline_time(timeline_sec)
        if detail is None:
            return None
        clip, source_time, _progress = detail
        return clip["path"], source_time

    def clip_detail_at_timeline_time(self, timeline_sec: float) -> tuple[dict, float, float] | None:
        source_timeline_sec = self.rendered_to_source_seconds(timeline_sec)
        if source_timeline_sec is None:
            return None
        rows = self.clip_rows()
        if not rows:
            return None
        cursor = 0.0
        for idx, row in enumerate(rows):
            start = cursor
            end = cursor + row["duration"]
            if source_timeline_sec <= end or idx == len(rows) - 1:
                local = max(0.0, min(row["duration"], source_timeline_sec - start))
                progress = local / row["duration"] if row["duration"] > 0 else 0.0
                return row, row["start"] + local, progress
            cursor = end
        return None

    def add_clip(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select video clips",
            self.last_dialog_dir("clips", self.current_project_root or Path.home()),
            "Video files (*.mp4 *.mov *.m4v *.mkv *.webm);;All files (*.*)",
        )
        if files:
            self.remember_dialog_path("clips", files[0])
        for path in files:
            row = self.clip_table.rowCount()
            self.clip_table.insertRow(row)
            self.clip_table.setItem(row, 0, QTableWidgetItem(path))
            self.clip_table.setItem(row, 1, QTableWidgetItem("0.0"))
            self.clip_table.setItem(row, 2, QTableWidgetItem("4.0"))
            self.set_clip_effect_widgets(row, {})
        self.update_timeline_from_inputs()
        self.load_preview_for_timeline()

    def project_data_from_ui(self) -> dict:
        if hasattr(self, "video_template_combo") and hasattr(self, "template_intro_enabled"):
            index = self.video_template_combo.currentIndex()
            if 0 <= index < len(self.template_configs):
                self.template_configs[index] = self.template_config_from_controls()

        bgm_start_mode = combo_value(self.bgm_start_mode, "auto") if hasattr(self, "bgm_start_mode") else "auto"
        if bgm_start_mode == "manual":
            bgm_start: str | float = self.bgm_start_spin.value()
        elif bgm_start_mode == "zero":
            bgm_start = 0.0
        else:
            bgm_start = "auto"

        selected_template = self.current_video_template() if hasattr(self, "video_template_combo") else "Basic Subtitle"
        template_config = self.current_template_config() if hasattr(self, "video_template_combo") else normalize_template_config(selected_template)
        return {
            "schema": "autocapcut.project.v1",
            "job": {
                "clips": self.clip_rows(),
                "captions": self.caption_rows(),
                "bgm": self.selected_bgm_path(),
                "output": self.output_edit.text().strip() if hasattr(self, "output_edit") else "",
                "volume": self.volume_spin.value() if hasattr(self, "volume_spin") else 0.42,
                "fade": self.fade_spin.value() if hasattr(self, "fade_spin") else 1.2,
                "video_encoder": combo_value(self.encoder_combo, "auto") if hasattr(self, "encoder_combo") else "auto",
                "video_quality": combo_value(self.quality_combo, "fast") if hasattr(self, "quality_combo") else "fast",
                "font_dir": self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "",
                "bgm_start": bgm_start,
                "main_caption_y": self.main_y_spin.value() if hasattr(self, "main_y_spin") else 1180,
                "addr_caption_y": self.addr_y_spin.value() if hasattr(self, "addr_y_spin") else 1390,
                "video_template": selected_template,
                "effect_template": selected_template,
                "template_config": template_config,
            },
            "template_configs": self.template_configs,
            "selected_template": selected_template,
            "render_settings": {
                "bgm_start_mode": bgm_start_mode,
                "bgm_start_seconds": self.bgm_start_spin.value() if hasattr(self, "bgm_start_spin") else 0.0,
                "video_encoder": combo_value(self.encoder_combo, "auto") if hasattr(self, "encoder_combo") else "auto",
                "video_quality": combo_value(self.quality_combo, "fast") if hasattr(self, "quality_combo") else "fast",
            },
            "ui": {
                "timeline_ms": self.timeline_slider.value() if hasattr(self, "timeline_slider") else 0,
                "guides": self.guides_button.isChecked() if hasattr(self, "guides_button") else False,
            },
            "assets": {
                "project_root": self.asset_root_edit.text().strip() if hasattr(self, "asset_root_edit") else "",
                "selected_bgm": self.selected_bgm_path(),
                "bgm_folder": self.bgm_folder_edit.text().strip() if hasattr(self, "bgm_folder_edit") else "",
                "broll_folder": self.broll_folder_edit.text().strip() if hasattr(self, "broll_folder_edit") else "",
                "font_folder": self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "",
            },
            "last_output": str(self.last_output) if self.last_output else "",
        }

    def save_project(self) -> None:
        if self.current_project_path is not None:
            self.write_project_file(self.current_project_path)
            return

        default_path = self.current_project_path or (Path(self.last_dialog_dir("projects", Path.home() / "Desktop")) / "autocapcut_project.autocapcut.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save AutoCapCut Project",
            str(default_path),
            "AutoCapCut Project (*.autocapcut.json);;JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return
        self.remember_dialog_path("projects", path)
        out = Path(path)
        lowered = out.name.lower()
        if not lowered.endswith(".autocapcut.json") and not lowered.endswith(".json"):
            out = out.with_name(out.name + ".autocapcut.json")
        self.write_project_file(out)

    def write_project_file(self, out: Path) -> None:
        try:
            out.write_text(json.dumps(self.project_data_from_ui(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Save Project Failed", str(exc))
            return
        self.remember_dialog_path("projects", out)
        self.app_settings().setValue("paths/last_project_file", str(out))
        self.current_project_path = out
        self.setWindowTitle(f"AutoCapCut - {out.name}")
        self.append_log(f"Saved project: {out}")

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load AutoCapCut Project",
            str(self.current_project_path or self.last_dialog_dir("projects", Path.home())),
            "AutoCapCut Project (*.autocapcut.json);;JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return
        self.remember_dialog_path("projects", path)
        project_path = Path(path)
        try:
            data = json.loads(project_path.read_text(encoding="utf-8-sig"))
            self.apply_project_data(data)
        except Exception as exc:
            QMessageBox.warning(self, "Load Project Failed", str(exc))
            return
        self.current_project_path = project_path
        self.app_settings().setValue("paths/last_project_file", str(project_path))
        self.setWindowTitle(f"AutoCapCut - {project_path.name}")
        self.append_log(f"Loaded project: {project_path}")

    def apply_project_data(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("Project file must be a JSON object.")
        job = data.get("job") if isinstance(data.get("job"), dict) else data
        if not isinstance(job, dict):
            raise ValueError("Project file is missing job data.")

        configs = data.get("template_configs")
        if isinstance(configs, list) and configs:
            self.template_configs = [item for item in configs if isinstance(item, dict)]
            self.refresh_template_combo()

        selected_template = str(data.get("selected_template") or job.get("video_template") or "Basic Subtitle")
        self.set_current_video_template(selected_template)
        if isinstance(job.get("template_config"), dict):
            index = self.video_template_combo.currentIndex() if hasattr(self, "video_template_combo") else -1
            if 0 <= index < len(self.template_configs):
                self.template_configs[index] = normalize_template_config(selected_template, job["template_config"])
                self.load_template_config_into_controls(self.template_configs[index])

        self.clip_table.blockSignals(True)
        self.clip_table.setRowCount(0)
        for clip in job.get("clips", []) or []:
            if not isinstance(clip, dict):
                continue
            row = self.clip_table.rowCount()
            self.clip_table.insertRow(row)
            self.clip_table.setItem(row, 0, QTableWidgetItem(str(clip.get("path", ""))))
            self.clip_table.setItem(row, 1, QTableWidgetItem(str(clip.get("start", 0.0))))
            self.clip_table.setItem(row, 2, QTableWidgetItem(str(clip.get("duration", 0.0))))
            self.set_clip_effect_widgets(row, clip)
        self.clip_table.blockSignals(False)

        self.caption_table.setRowCount(0)
        for caption in job.get("captions", []) or []:
            if isinstance(caption, dict):
                self.add_caption_row(caption)
        if self.caption_table.rowCount():
            self.caption_table.selectRow(0)

        if hasattr(self, "bgm_edit"):
            self.bgm_edit.setText(str(job.get("bgm", "")))
        if hasattr(self, "bgm_combo"):
            self.set_bgm_combo_path(str(job.get("bgm", "")))
        if hasattr(self, "output_edit"):
            self.output_edit.setText(str(job.get("output", "")))
        if hasattr(self, "volume_spin"):
            self.volume_spin.setValue(float(job.get("volume", self.volume_spin.value()) or self.volume_spin.value()))
        if hasattr(self, "fade_spin"):
            self.fade_spin.setValue(float(job.get("fade", self.fade_spin.value()) or self.fade_spin.value()))
        if hasattr(self, "encoder_combo"):
            render_settings = data.get("render_settings", {}) if isinstance(data.get("render_settings"), dict) else {}
            self.set_combo_data(self.encoder_combo, str(render_settings.get("video_encoder") or job.get("video_encoder") or "auto"))
        if hasattr(self, "quality_combo"):
            render_settings = data.get("render_settings", {}) if isinstance(data.get("render_settings"), dict) else {}
            self.set_combo_data(self.quality_combo, str(render_settings.get("video_quality") or job.get("video_quality") or "fast"))
        if hasattr(self, "main_y_spin"):
            self.main_y_spin.setValue(int(job.get("main_caption_y", self.main_y_spin.value()) or self.main_y_spin.value()))
        if hasattr(self, "addr_y_spin"):
            self.addr_y_spin.setValue(int(job.get("addr_caption_y", self.addr_y_spin.value()) or self.addr_y_spin.value()))
        if hasattr(self, "bgm_start_mode"):
            render_settings = data.get("render_settings", {}) if isinstance(data.get("render_settings"), dict) else {}
            mode = str(render_settings.get("bgm_start_mode") or "auto")
            self.set_combo_data(self.bgm_start_mode, mode)
            self.bgm_start_spin.setValue(float(render_settings.get("bgm_start_seconds", 0.0) or 0.0))
        assets = data.get("assets", {}) if isinstance(data.get("assets"), dict) else {}
        if hasattr(self, "asset_root_edit"):
            root_text = str(assets.get("project_root", ""))
            self.asset_root_edit.setText(root_text)
            self.current_project_root = Path(root_text) if root_text else None
        if hasattr(self, "bgm_folder_edit"):
            self.bgm_folder_edit.setText(str(assets.get("bgm_folder", "")))
        if hasattr(self, "broll_folder_edit"):
            self.broll_folder_edit.setText(str(assets.get("broll_folder", "")))
        if hasattr(self, "font_folder_edit"):
            self.font_folder_edit.setText(str(assets.get("font_folder", "")))
            self.populate_font_choices(self.font_asset_paths())
        selected_bgm = str(assets.get("selected_bgm") or job.get("bgm", ""))
        if hasattr(self, "bgm_combo"):
            self.set_bgm_combo_path(selected_bgm)

        ui = data.get("ui", {}) if isinstance(data.get("ui"), dict) else {}
        if hasattr(self, "guides_button"):
            self.guides_button.setChecked(bool(ui.get("guides", False)))
        self.last_output = Path(str(data.get("last_output"))) if data.get("last_output") else None
        self.open_button.setEnabled(bool(self.last_output))

        self._preview_clip_key = None
        self.update_timeline_from_inputs()
        if hasattr(self, "timeline_slider"):
            value = int(ui.get("timeline_ms", 0) or 0)
            value = max(self.timeline_slider.minimum(), min(self.timeline_slider.maximum(), value))
            self.timeline_slider.setValue(value)
        self.update_caption_preview()
        self.load_preview_for_timeline()

    def refresh_template_combo(self) -> None:
        if not hasattr(self, "video_template_combo"):
            return
        self.video_template_combo.blockSignals(True)
        self.video_template_combo.clear()
        for template in self.template_configs:
            self.video_template_combo.addItem(str(template.get("name") or template.get("id") or "Template"))
        self.video_template_combo.blockSignals(False)

    def set_current_video_template(self, template_name: str) -> None:
        if not hasattr(self, "video_template_combo"):
            return
        target = template_key(template_name)
        found = -1
        for index, config in enumerate(self.template_configs):
            names = {
                template_key(str(config.get("name", ""))),
                template_key(str(config.get("id", ""))),
            }
            if target in names:
                found = index
                break
        if found < 0:
            found = max(0, self.video_template_combo.findText(template_name))
        if found >= 0:
            self.video_template_combo.setCurrentIndex(found)
            if 0 <= found < len(self.template_configs):
                self.load_template_config_into_controls(self.template_configs[found])
                self.apply_template_side_effects(self.template_configs[found])

    def remove_selected_clips(self) -> None:
        rows = sorted({idx.row() for idx in self.clip_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.clip_table.removeRow(row)
        self.update_timeline_from_inputs()
        self.load_preview_for_timeline()

    def import_segment_plan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Segment Plan JSON",
            self.last_dialog_dir("segment_plans", self.current_project_root or Path.home()),
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return
        self.remember_dialog_path("segment_plans", path)
        try:
            plan = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Segment Plan", f"Could not read JSON:\n{exc}")
            return
        try:
            candidates = self.segment_candidates_from_plan(plan)
            self.apply_segment_plan(plan, candidates)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Segment Plan", str(exc))

    def segment_candidates_from_plan(self, plan: dict) -> list[dict]:
        if not isinstance(plan, dict):
            raise ValueError("Segment Plan must be a JSON object.")
        raw_segments = plan.get("segments")
        if isinstance(raw_segments, dict):
            candidates = [raw_segments]
        elif isinstance(raw_segments, list):
            candidates = [item for item in raw_segments if isinstance(item, dict)]
        elif "source_start" in plan or "source_end" in plan:
            candidates = [plan]
        else:
            candidates = []
        if not candidates:
            raise ValueError("Segment Plan needs a non-empty segments array.")
        return candidates

    def segment_candidate_label(self, candidate: dict, idx: int) -> str:
        seg_id = str(candidate.get("id") or f"seg_{idx:03d}")
        title = str(candidate.get("title") or candidate.get("name") or "Untitled")
        duration = self.segment_candidate_duration(candidate)
        score = candidate.get("hook_score")
        score_text = f" score={score}" if score is not None else ""
        return f"{seg_id} | {duration:.1f}s | {title}{score_text}"

    def segment_candidate_duration(self, candidate: dict) -> float:
        try:
            rows = self.candidate_clip_rows({}, candidate)
            return sum(row["duration"] for row in rows)
        except Exception:
            duration = self.seconds_value(candidate.get("duration"), 0.0)
            return duration or 0.0

    def apply_segment_plan(self, plan: dict, candidates: list[dict]) -> None:
        clip_rows: list[dict] = []
        caption_rows: list[dict] = []
        skipped: list[str] = []
        timeline_cursor = 0.0

        for idx, candidate in enumerate(candidates, start=1):
            try:
                candidate_clips = self.candidate_clip_rows(plan, candidate)
                if not candidate_clips:
                    raise ValueError("missing valid source range")
                candidate_duration = sum(row["duration"] for row in candidate_clips)
                for caption in self.candidate_caption_rows(candidate, candidate_duration):
                    shifted = dict(caption)
                    shifted["start"] = round(float(caption["start"]) + timeline_cursor, 3)
                    shifted["end"] = round(float(caption["end"]) + timeline_cursor, 3)
                    caption_rows.append(shifted)
                clip_rows.extend(candidate_clips)
                timeline_cursor += candidate_duration
            except Exception as exc:
                skipped.append(f"{candidate.get('id') or idx}: {exc}")

        if not clip_rows:
            raise ValueError("Segment Plan does not contain any valid source ranges.")

        self.clip_table.blockSignals(True)
        self.clip_table.setRowCount(0)
        for clip in clip_rows:
            row = self.clip_table.rowCount()
            self.clip_table.insertRow(row)
            self.clip_table.setItem(row, 0, QTableWidgetItem(clip["path"]))
            self.clip_table.setItem(row, 1, QTableWidgetItem(f"{clip['start']:.3f}"))
            self.clip_table.setItem(row, 2, QTableWidgetItem(f"{clip['duration']:.3f}"))
            self.set_clip_effect_widgets(row, clip)
        self.clip_table.blockSignals(False)

        self.caption_table.setRowCount(0)
        for caption in caption_rows:
            self.add_caption_row(caption)
        if self.caption_table.rowCount():
            self.caption_table.selectRow(0)

        self._preview_clip_key = None
        self.timeline_slider.setValue(0)
        self.update_timeline_from_inputs()
        self.update_caption_preview()
        self.load_preview_for_timeline()
        self.append_log(f"Imported {len(clip_rows)} clip segment(s) into one timeline ({timeline_cursor:.2f}s).")
        if skipped:
            self.append_log("Skipped invalid segment(s):")
            for item in skipped[:8]:
                self.append_log(f"- {item}")

    def candidate_clip_rows(self, plan: dict, candidate: dict) -> list[dict]:
        source_video = str(candidate.get("source_video") or plan.get("source_video") or "").strip()
        raw_ranges = candidate.get("source_ranges") or candidate.get("clips")
        if isinstance(raw_ranges, list) and raw_ranges:
            ranges = [item for item in raw_ranges if isinstance(item, dict)]
        else:
            ranges = [candidate]

        rows: list[dict] = []
        for item in ranges:
            path = str(item.get("source_video") or source_video).strip()
            start = self.seconds_value(item.get("source_start", item.get("start", item.get("in"))), 0.0)
            end = self.seconds_value(item.get("source_end", item.get("end")), None)
            duration = self.seconds_value(item.get("duration", item.get("dur")), None)
            if duration is None and end is not None:
                duration = end - start
            if duration is None or duration <= 0:
                continue
            if not path:
                raise ValueError("Segment Plan is missing source_video.")
            rows.append({"path": path, "start": max(0.0, start), "duration": duration})
        return rows

    def candidate_caption_rows(self, candidate: dict, total_duration: float) -> list[dict]:
        raw_blocks = candidate.get("caption_blocks") or candidate.get("captions") or []
        if isinstance(raw_blocks, dict):
            raw_blocks = list(raw_blocks.values())
        if not isinstance(raw_blocks, list):
            return []

        source_start = self.seconds_value(candidate.get("source_start"), 0.0)
        source_end = self.seconds_value(candidate.get("source_end"), None)
        if source_end is None:
            source_end = source_start + total_duration
        captions: list[dict] = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                continue
            start = self.seconds_value(block.get("start", block.get("source_start")), 0.0)
            end = self.seconds_value(block.get("end", block.get("source_end")), None)
            if source_end is not None and start >= source_start and end is not None and end <= source_end + 0.25:
                start -= source_start
                end -= source_start
            if end is None:
                end = start + 2.0
            start = max(0.0, min(total_duration, start))
            end = max(start, min(total_duration, end))
            if end <= start:
                continue

            segments = block.get("segments")
            if not segments:
                text = str(block.get("text") or block.get("caption") or "").strip()
                segments = [[text, "w"]] if text else []
            kind = str(block.get("kind", "main")).strip().lower()
            captions.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "kind": kind if kind in KIND_OPTIONS else "main",
                    "segments": self.normalize_segments(segments),
                }
            )
        return [caption for caption in captions if caption["segments"]]

    def seconds_value(self, value, default):
        if value is None or value == "":
            return default
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        try:
            return float(text)
        except ValueError:
            pass
        if ":" in text:
            parts = text.split(":")
            try:
                seconds = 0.0
                for part in parts:
                    seconds = seconds * 60 + float(part)
                return seconds
            except ValueError:
                return default
        return default

    def segment_output_path(self, candidate: dict, idx: int = 1) -> Path:
        current = Path(self.output_edit.text().strip() or str(Path.home() / "Desktop" / "autocapcut_short.mp4"))
        out_dir = current.parent if str(current.parent) else Path.home() / "Desktop"
        raw_name = str(candidate.get("id") or candidate.get("title") or f"seg_{idx:03d}")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._-") or "autocapcut_short"
        return out_dir / f"{safe}.mp4"

    def first_clip_path_and_time(self) -> tuple[str, float] | None:
        if self.clip_table.rowCount() == 0:
            return None
        file_item = self.clip_table.item(0, 0)
        start_item = self.clip_table.item(0, 1)
        if not file_item or not file_item.text().strip():
            return None
        try:
            start = float(start_item.text()) if start_item and start_item.text().strip() else 0.0
        except ValueError:
            start = 0.0
        return file_item.text().strip(), start

    def schedule_preview_load(self, *args) -> None:
        del args
        self.update_timeline_from_inputs()
        self._preview_timer.start(150)

    def load_preview_for_timeline(self) -> None:
        captions = self.caption_rows_with_table_rows() if hasattr(self, "caption_table") else []
        active = self.caption_at_timeline_time_with_row(captions)
        active_row, active_caption = active if active is not None else (-1, {})
        kind = str(active_caption.get("kind", "main"))
        segments = self.normalize_segments(active_caption.get("segments", []))
        segments = self.template_preview_segments(self.current_video_template(), segments, active_row)
        main_y = self.main_y_spin.value() if hasattr(self, "main_y_spin") else 1180
        addr_y = self.addr_y_spin.value() if hasattr(self, "addr_y_spin") else 1390

        detail = self.clip_detail_at_timeline_time(self.timeline_slider.value() / 1000.0)
        if detail is None:
            template_config = self.current_template_config()
            intro = template_config.get("intro", {}) if isinstance(template_config, dict) else {}
            if bool(intro.get("enabled", False)) and str(intro.get("type", "")).lower() == "hook_card" and captions:
                fallback_title = caption_text_from_segments(self.normalize_segments(captions[0][1].get("segments", [])))
                hook_title = str(intro.get("hook_text", "") or fallback_title or "今天的重點")
                hook_niche = str(intro.get("niche", "teaching") or "teaching")
                badge_text = str(intro.get("badge_text", "") or "")
                bg_path = None
                first_clip = self.first_clip_path_and_time()
                if first_clip is not None:
                    first_clip_path, first_clip_start = first_clip
                    try:
                        path = Path(first_clip_path)
                        if path.exists():
                            bg_path = extract_preview_frame(path, first_clip_start)
                    except Exception as exc:
                        self.append_log(f"Hook background frame failed: {exc}")
                key = f"hook|{template_config!r}|{hook_title}|{hook_niche}|{badge_text}|{bg_path}"
                if key == self._preview_clip_key:
                    return
                self._preview_clip_key = key
                try:
                    frame = render_hook_preview_frame(hook_title, hook_niche, badge_text=badge_text, bg_path=bg_path)
                    self.preview.set_background(str(frame), has_captions=True)
                except Exception as exc:
                    self.preview.set_background(None, has_captions=False)
                    self.append_log(f"Hook preview failed: {exc}")
                return
            if hasattr(self, "preview"):
                self.preview.set_background(None, has_captions=False)
            return
        clip, start, clip_progress = detail
        clip_path = str(clip["path"])
        motion = str(clip.get("motion", "none"))
        if not Path(clip_path).exists():
            if hasattr(self, "preview"):
                self.preview.set_background(None, has_captions=False)
            return
        font_dir = self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else ""
        key = f"{clip_path}|{start:.3f}|{segments!r}|{kind}|{main_y}|{addr_y}|{self.current_video_template()}|{self.current_template_config()!r}|{font_dir}|{motion}|{clip_progress:.3f}"
        if key == self._preview_clip_key:
            return
        self._preview_clip_key = key
        self.load_preview_frame(
            clip_path,
            start,
            segments,
            kind,
            main_y,
            addr_y,
            self.current_video_template(),
            False,
            self.current_template_config(),
            motion,
            clip_progress,
        )

    def load_preview_frame(
        self,
        clip_path: str,
        start: float,
        segments: list[list[str]] | None = None,
        kind: str = "main",
        main_y: int = 1180,
        addr_y: int = 1390,
        video_template: str = "Basic Subtitle",
        accurate: bool = False,
        template_config: dict | None = None,
        motion: str = "none",
        clip_progress: float = 0.0,
    ) -> None:
        if self._preview_thread_is_running():
            self._pending_preview_request = (clip_path, start, segments or [], kind, main_y, addr_y, video_template, accurate, template_config or {}, motion, clip_progress)
            return
        thread = QThread(self)
        worker = PreviewFrameWorker(
            clip_path,
            start,
            segments=segments or [],
            kind=kind,
            main_y=main_y,
            addr_y=addr_y,
            video_template=video_template,
            template_config=template_config or self.current_template_config(),
            motion=motion,
            clip_progress=clip_progress,
            font_dir=self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "",
            accurate=accurate,
        )
        self._preview_thread = thread
        self._preview_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.append_log)
        worker.finished.connect(self.preview_loaded)
        worker.failed.connect(self.preview_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda thread=thread: self._clear_preview_thread(thread))
        thread.start()

    def _preview_thread_is_running(self) -> bool:
        if self._preview_thread is None:
            return False
        try:
            return self._preview_thread.isRunning()
        except RuntimeError:
            self._preview_thread = None
            self._preview_worker = None
            return False

    def _clear_preview_thread(self, thread: QThread) -> None:
        if self._preview_thread is thread:
            self._preview_thread = None
            self._preview_worker = None
            pending = self._pending_preview_request
            self._pending_preview_request = None
            if pending is not None:
                clip_path, start, segments, kind, main_y, addr_y, video_template, accurate, template_config, motion, clip_progress = pending
                QTimer.singleShot(
                    0,
                    lambda: self.load_preview_frame(
                        clip_path,
                        start,
                        segments,
                        kind,
                        main_y,
                        addr_y,
                        video_template,
                        accurate,
                        template_config,
                        motion,
                        clip_progress,
                    ),
                )

    def preview_loaded(self, image_path: str) -> None:
        if self.is_preview_playing():
            return
        self.preview.set_background(image_path, has_captions=False)

    def preview_failed(self, message: str) -> None:
        self.preview.set_background(None, has_captions=False)
        self.append_log("Preview frame failed")
        self.append_log(message.splitlines()[0] if message else "Unknown preview error")

    def pick_bgm(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select BGM",
            self.last_dialog_dir("bgm", self.current_project_root or Path.home()),
            "Audio files (*.mp3 *.wav *.m4a *.aac *.flac);;All files (*.*)",
        )
        if path:
            self.remember_dialog_path("bgm", path)
            self.set_bgm_combo_path(path)

    def selected_bgm_path(self) -> str:
        if hasattr(self, "bgm_combo"):
            return combo_value(self.bgm_combo, "")
        return self.bgm_edit.text().strip() if hasattr(self, "bgm_edit") else ""

    def selected_bgm_changed(self, *_args) -> None:
        if hasattr(self, "bgm_edit"):
            self.bgm_edit.setText(self.selected_bgm_path())
        self._audio_clip_path = None

    def populate_bgm_choices(self, paths: list[Path]) -> None:
        if not hasattr(self, "bgm_combo"):
            return
        current = self.selected_bgm_path()
        self.bgm_combo.blockSignals(True)
        self.bgm_combo.clear()
        self.bgm_combo.addItem("Use source audio (no BGM)")
        self.bgm_combo.setItemData(0, "")
        for path in paths:
            self.bgm_combo.addItem(path.name)
            self.bgm_combo.setItemData(self.bgm_combo.count() - 1, str(path))
        self.bgm_combo.blockSignals(False)
        self.set_bgm_combo_path(current)

    def set_bgm_combo_path(self, path: str) -> None:
        if not hasattr(self, "bgm_combo"):
            if hasattr(self, "bgm_edit"):
                self.bgm_edit.setText(path)
            return
        path = str(path or "")
        if path:
            idx = self.bgm_combo.findData(path)
            if idx < 0:
                self.bgm_combo.addItem(Path(path).name)
                self.bgm_combo.setItemData(self.bgm_combo.count() - 1, path)
                idx = self.bgm_combo.count() - 1
            self.bgm_combo.setCurrentIndex(idx)
        elif self.bgm_combo.count():
            self.bgm_combo.setCurrentIndex(0)
        if hasattr(self, "bgm_edit"):
            self.bgm_edit.setText(path)

    def font_combo_value(self) -> str:
        if not hasattr(self, "template_font_edit"):
            return "Noto Sans TC"
        data = self.template_font_edit.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        return self.template_font_edit.currentText().strip() or "Noto Sans TC"

    def _font_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def resolve_font_family(self, value: str) -> str:
        family = (value or "Noto Sans TC").strip() or "Noto Sans TC"
        if not hasattr(self, "template_font_edit"):
            return family

        idx = self.template_font_edit.findData(family)
        if idx >= 0:
            data = self.template_font_edit.itemData(idx)
            return str(data or self.template_font_edit.itemText(idx)).strip() or family
        idx = self.template_font_edit.findText(family)
        if idx >= 0:
            data = self.template_font_edit.itemData(idx)
            return str(data or self.template_font_edit.itemText(idx)).strip() or family

        wanted = self._font_key(family)
        for path in self.font_asset_paths():
            path_keys = {self._font_key(path.stem), self._font_key(path.name)}
            for font_family in self.font_families_for_path(path):
                family_key = self._font_key(font_family)
                if not family_key:
                    continue
                if wanted in path_keys or wanted == family_key or wanted.startswith(family_key):
                    return font_family
        return family

    def set_font_combo_value(self, family: str) -> None:
        if not hasattr(self, "template_font_edit"):
            return
        family = self.resolve_font_family(family)
        idx = self.template_font_edit.findData(family)
        if idx < 0:
            idx = self.template_font_edit.findText(family)
        if idx < 0:
            self.template_font_edit.addItem(family)
            self.template_font_edit.setItemData(self.template_font_edit.count() - 1, family)
            idx = self.template_font_edit.count() - 1
        self.template_font_edit.setCurrentIndex(idx)

    def font_families_for_path(self, path: Path) -> list[str]:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families = [
                family.strip()
                for family in QFontDatabase.applicationFontFamilies(font_id)
                if family.strip() and self._font_key(family)
            ]
            if families:
                return families
        return [path.stem]

    def populate_font_choices(self, paths: list[Path]) -> None:
        if not hasattr(self, "template_font_edit"):
            return
        current = self.font_combo_value()
        families: list[str] = ["Noto Sans TC"]
        seen = {families[0].lower()}
        for path in paths:
            for family in self.font_families_for_path(path):
                key = family.lower()
                if key not in seen:
                    families.append(family)
                    seen.add(key)
        self.template_font_edit.blockSignals(True)
        self.template_font_edit.clear()
        for family in families:
            self.template_font_edit.addItem(family)
            self.template_font_edit.setItemData(self.template_font_edit.count() - 1, family)
        self.template_font_edit.blockSignals(False)
        self.set_font_combo_value(current)

    def font_asset_paths(self) -> list[Path]:
        if not hasattr(self, "font_folder_edit"):
            return []
        root = Path(self.font_folder_edit.text().strip()).expanduser()
        if not root.exists():
            return []
        return [path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}]

    def pick_output(self) -> None:
        current = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else None
        start = current if current else Path(self.last_dialog_dir("outputs", Path.home() / "Desktop")) / "autocapcut_short.mp4"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select output MP4",
            str(start),
            "MP4 video (*.mp4);;All files (*.*)",
        )
        if path:
            self.remember_dialog_path("outputs", path)
            self.output_edit.setText(path)

    def safe_project_prefix(self, name: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name).strip().strip(".")
        return cleaned or "Project"

    def next_project_root(self, base: Path, prefix: str) -> Path:
        safe_prefix = self.safe_project_prefix(prefix)
        for index in range(1, 1000):
            candidate = base / f"{safe_prefix}{index:03d}"
            if not candidate.exists():
                return candidate
        raise ValueError(f"No available project folder under {base}")

    def new_clip_project(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("新建剪輯專案")
        dialog.resize(560, 180)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        default_base = self.current_project_root.parent if self.current_project_root else Path(self.last_dialog_dir("project_base", Path.home() / "Desktop" / "AutoCapCutProjects"))
        name_edit = QLineEdit()
        name_edit.setText("Project")
        base_edit = QLineEdit()
        base_edit.setText(str(default_base))
        folder_preview = QLabel()

        def update_preview() -> None:
            base = Path(base_edit.text().strip() or str(default_base)).expanduser()
            try:
                folder_preview.setText(str(self.next_project_root(base, name_edit.text())))
            except Exception as exc:
                folder_preview.setText(str(exc))

        def browse_base() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog,
                "Select project base folder",
                base_edit.text() or self.last_dialog_dir("project_base", default_base),
            )
            if selected:
                self.remember_dialog_path("project_base", selected)
                base_edit.setText(selected)

        name_edit.textChanged.connect(update_preview)
        base_edit.textChanged.connect(update_preview)
        form.addRow("專案名稱", name_edit)
        form.addRow("Base Path", self._path_row(base_edit, browse_base))
        form.addRow("建立目錄", folder_preview)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        create_button = PrimaryPushButton("Create")
        cancel_button = QPushButton("Cancel")
        create_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(create_button)
        layout.addLayout(buttons)
        update_preview()

        if dialog.exec() != QDialog.Accepted:
            return

        base = Path(base_edit.text().strip()).expanduser()
        prefix = self.safe_project_prefix(name_edit.text())
        root = self.next_project_root(base, prefix)
        assets = root / "assets"
        bgm = assets / "bgm"
        broll = assets / "b-roll"
        fonts = assets / "fonts"
        logos = assets / "logos"
        exports = root / "exports"
        plans = root / "plans"
        for folder in (root, assets, bgm, broll, fonts, logos, exports, plans):
            folder.mkdir(parents=True, exist_ok=True)
        for source in (IG_LOGO_PATH, THREADS_LOGO_PATH):
            if source.exists():
                target = logos / source.name
                if not target.exists():
                    try:
                        target.write_bytes(source.read_bytes())
                    except OSError:
                        pass

        self.current_project_root = root
        if hasattr(self, "asset_root_edit"):
            self.asset_root_edit.setText(str(root))
        if hasattr(self, "bgm_folder_edit"):
            self.bgm_folder_edit.setText(str(bgm))
        if hasattr(self, "broll_folder_edit"):
            self.broll_folder_edit.setText(str(broll))
        if hasattr(self, "font_folder_edit"):
            self.font_folder_edit.setText(str(fonts))
        if hasattr(self, "output_edit"):
            self.output_edit.setText(str(exports / "autocapcut_short.mp4"))

        self.current_project_path = root / "autocapcut_project.autocapcut.json"
        self.write_project_file(self.current_project_path)
        self.append_log(f"Clip project folders ready: {root}")
        self.scan_asset_folders()

    def pick_asset_folder(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select asset folder",
            edit.text() or self.last_dialog_dir("assets", self.current_project_root or Path.home()),
        )
        if path:
            self.remember_dialog_path("assets", path)
            edit.setText(path)

    def scan_asset_folders(self) -> None:
        if not hasattr(self, "assets_table"):
            return
        self.assets_table.setRowCount(0)
        bgm_paths: list[Path] = []
        font_paths: list[Path] = []
        scans = [
            ("BGM", self.bgm_folder_edit.text().strip() if hasattr(self, "bgm_folder_edit") else "", {".mp3", ".wav", ".m4a", ".aac", ".flac"}),
            ("B-roll", self.broll_folder_edit.text().strip() if hasattr(self, "broll_folder_edit") else "", {".mp4", ".mov", ".m4v", ".mkv", ".webm"}),
            ("Font", self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "", {".ttf", ".otf", ".ttc"}),
        ]
        count = 0
        if not any(folder for _asset_type, folder, _suffixes in scans):
            self.populate_bgm_choices([])
            self.populate_font_choices([])
            count = self.scan_video_kit_assets()
            self.append_log(f"Scanned {count} video-autopilot-kit asset item(s).")
            return
        for asset_type, folder, suffixes in scans:
            root = Path(folder).expanduser() if folder else None
            if root is None or not root.exists():
                continue
            for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes):
                row = self.assets_table.rowCount()
                self.assets_table.insertRow(row)
                self.assets_table.setItem(row, 0, QTableWidgetItem(asset_type))
                self.assets_table.setItem(row, 1, QTableWidgetItem(path.name))
                self.assets_table.setItem(row, 2, QTableWidgetItem(self.probe_duration_label(path) if asset_type != "Font" else ""))
                self.assets_table.setItem(row, 3, QTableWidgetItem(str(path)))
                if asset_type == "BGM":
                    bgm_paths.append(path)
                elif asset_type == "Font":
                    font_paths.append(path)
                count += 1
        self.populate_bgm_choices(bgm_paths)
        self.populate_font_choices(font_paths)
        self.append_log(f"Scanned {count} asset file(s).")

    def scan_video_kit_assets(self) -> int:
        try:
            from autocapcut_app.paths import ensure_vendor_on_path

            ensure_vendor_on_path()
            from silent_vlog_maker.asset_scanner import scan_all_assets

            index = scan_all_assets(write=False, backup=False)
        except Exception as exc:
            self.append_log(f"video-autopilot-kit asset scan failed: {exc}")
            return 0

        count = 0
        sections = [
            ("BGM", index.get("bgm_actual", {})),
            ("Font", index.get("fonts_actual", {})),
            ("B-roll", index.get("broll_actual", {}) or index.get("broll_files", {}) or index.get("broll", {})),
            ("B-roll", index.get("gameplay_actual", {}) or index.get("gameplay_files", {}) or index.get("gameplay", {})),
        ]
        for asset_type, items in sections:
            if not isinstance(items, dict):
                continue
            for name, meta in items.items():
                meta = meta if isinstance(meta, dict) else {}
                row = self.assets_table.rowCount()
                self.assets_table.insertRow(row)
                self.assets_table.setItem(row, 0, QTableWidgetItem(asset_type))
                self.assets_table.setItem(row, 1, QTableWidgetItem(str(name)))
                duration = meta.get("duration_sec", "")
                self.assets_table.setItem(row, 2, QTableWidgetItem(f"{duration}s" if duration != "" else ""))
                self.assets_table.setItem(row, 3, QTableWidgetItem(str(meta.get("filepath", ""))))
                count += 1
        return count

    def probe_duration_label(self, path: Path) -> str:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if result.returncode == 0 and result.stdout.strip():
                return f"{float(result.stdout.strip()):.2f}s"
        except Exception:
            pass
        return ""

    def broll_asset_paths(self) -> list[str]:
        paths: list[str] = []
        if hasattr(self, "assets_table"):
            for row in range(self.assets_table.rowCount()):
                type_item = self.assets_table.item(row, 0)
                path_item = self.assets_table.item(row, 3)
                if type_item and path_item and type_item.text() == "B-roll":
                    paths.append(path_item.text())
        if not paths and hasattr(self, "broll_folder_edit"):
            root = Path(self.broll_folder_edit.text().strip()).expanduser()
            if root.exists():
                paths = [str(path) for path in sorted(root.rglob("*")) if path.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv", ".webm"}]
        return paths

    def match_broll_to_captions(self) -> None:
        brolls = self.broll_asset_paths()
        if not brolls:
            QMessageBox.information(self, "B-roll Matcher", "Select or scan a B-roll folder first.")
            return
        try:
            from autocapcut_app.paths import ensure_vendor_on_path

            ensure_vendor_on_path()
            from capcut_helpers.caption_broll_matcher import match_brolls_to_captions

            captions = [
                {
                    "text": caption_text_from_segments(self.normalize_segments(caption.get("segments", []))),
                    "start_us": int(float(caption.get("start", 0.0)) * 1_000_000),
                    "duration_us": int(max(0.0, float(caption.get("end", 0.0)) - float(caption.get("start", 0.0))) * 1_000_000),
                }
                for caption in self.caption_rows()
            ]
            matches = match_brolls_to_captions(captions, brolls)
        except Exception as exc:
            QMessageBox.warning(self, "B-roll Matcher Failed", str(exc))
            return

        self.broll_match_table.setRowCount(0)
        for match in matches:
            row = self.broll_match_table.rowCount()
            self.broll_match_table.insertRow(row)
            self.broll_match_table.setItem(row, 0, QTableWidgetItem(str(match.get("caption_text", ""))))
            self.broll_match_table.setItem(row, 1, QTableWidgetItem(Path(str(match.get("best_broll", ""))).name))
            self.broll_match_table.setItem(row, 2, QTableWidgetItem(f"{float(match.get('score', 0.0)):.2f}"))
            self.broll_match_table.setItem(row, 3, QTableWidgetItem(str(match.get("topic_label", ""))))
            self.broll_match_table.setItem(row, 4, QTableWidgetItem(str(match.get("best_broll", ""))))
        self.append_log(f"Matched {len(matches)} caption(s) to B-roll candidates.")

    def collect_job_data(self) -> dict:
        clips = self.clip_rows()
        bgm_start_mode = combo_value(self.bgm_start_mode, "auto")
        if bgm_start_mode == "manual":
            bgm_start: str | float = self.bgm_start_spin.value()
        elif bgm_start_mode == "zero":
            bgm_start = 0.0
        else:
            bgm_start = "auto"
        template_config = self.current_template_config() if hasattr(self, "video_template_combo") else normalize_template_config("Basic Subtitle")
        template_config = dict(template_config)
        template_config["caption"] = dict(template_config.get("caption", {}))
        template_config["audio"] = dict(template_config.get("audio", {}))
        template_config["caption"]["main_y"] = self.main_y_spin.value()
        template_config["caption"]["addr_y"] = self.addr_y_spin.value()
        template_config["audio"]["volume"] = self.volume_spin.value()
        template_config["audio"]["fade"] = self.fade_spin.value()
        template_config["audio"]["bgm_start"] = bgm_start_mode
        template_config["audio"]["bgm_start_seconds"] = self.bgm_start_spin.value()
        template_config["copyright"] = self.copyright_config_from_controls()
        return {
            "clips": clips,
            "captions": self.caption_rows(),
            "bgm": self.selected_bgm_path(),
            "output": self.output_edit.text().strip(),
            "volume": self.volume_spin.value(),
            "fade": self.fade_spin.value(),
            "video_encoder": combo_value(self.encoder_combo, "auto"),
            "video_quality": combo_value(self.quality_combo, "fast"),
            "font_dir": self.font_folder_edit.text().strip() if hasattr(self, "font_folder_edit") else "",
            "bgm_start": bgm_start,
            "main_caption_y": self.main_y_spin.value(),
            "addr_caption_y": self.addr_y_spin.value(),
            "video_template": self.video_template_combo.currentText()
            if hasattr(self, "video_template_combo")
            else "Basic Subtitle",
            "effect_template": self.video_template_combo.currentText()
            if hasattr(self, "video_template_combo")
            else "Basic Subtitle",
            "template_config": template_config,
        }

    def start_render(self) -> None:
        try:
            data = self.collect_job_data()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Input", str(exc))
            return

        if self.is_preview_playing():
            self.stop_preview_playback()
        self.show_render_log_dialog()
        if self.log_edit is not None:
            self.log_edit.clear()
        self.update_render_progress(0, "Preparing render")
        self.append_log("Starting render job")
        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.last_output = None

        self._thread = QThread(self)
        self._worker = ShortVideoWorker(data)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.progress.connect(self.update_render_progress)
        self._worker.finished.connect(self.render_finished)
        self._worker.failed.connect(self.render_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def append_log(self, message: str) -> None:
        if self.log_edit is not None:
            self.log_edit.appendPlainText(message)

    def update_render_progress(self, value: int, message: str = "") -> None:
        value = max(0, min(100, int(value)))
        if self.render_progress is not None:
            self.render_progress.setRange(0, 100)
            self.render_progress.setValue(value)
        if self.render_progress_label is not None and message:
            self.render_progress_label.setText(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_preview_playback()
        self.preview_engine_stop.emit()
        if self._preview_engine_thread is not None:
            try:
                self._preview_engine_thread.quit()
                self._preview_engine_thread.wait(1200)
            except RuntimeError:
                pass
        super().closeEvent(event)

    def render_finished(self, output: str) -> None:
        first_output = output.splitlines()[0] if output else ""
        self.last_output = Path(first_output)
        self.update_render_progress(100, "Render complete")
        self.append_log(f"Done: {output}")
        self.run_button.setEnabled(True)
        self.open_button.setEnabled(True)

    def render_failed(self, message: str) -> None:
        self.update_render_progress(0, "Render failed")
        self.append_log("Render failed")
        self.append_log(message)
        self.run_button.setEnabled(True)
        QMessageBox.critical(self, "Render Failed", message.splitlines()[0] if message else "Unknown error")

    def open_output_folder(self) -> None:
        if self.last_output:
            os.startfile(str(self.last_output.parent))


def main() -> int:
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AutoCapCut")
    app.setWindowIcon(app_icon())
    app.setFont(QFont(UI_FONT_FAMILY, 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
