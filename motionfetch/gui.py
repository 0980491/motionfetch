"""Qt interface: convert with a draggable crop box and a live preview,
then manage the library (play, link commands, export, delete).

Needs PySide6 (`pipx install 'motionfetch[gui]'`). Everything the GUI does
goes through the same modules as the CLI, so both stay in sync.

Niceties: dark/light theme (persisted), KDE file dialogs when kdialog is
around, and a desktop menu entry that installs itself on first launch.
"""

import io
import os
import random
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageDraw
from PySide6.QtCore import (
    QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QIcon, QImage, QPainter, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import convert, export, generators, library, links, player

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}

FILE_FILTER = (
    "Videos and images (*.mp4 *.mkv *.webm *.mov *.avi *.m4v "
    "*.gif *.png *.jpg *.jpeg *.webp)"
)

# ── theming ──

PALETTES = {
    "dark": dict(
        base="#1e1e2e", mantle="#181825", surface="#313244",
        surface2="#45475a", text="#cdd6f4", sub="#a6adc8",
        accent="#89b4fa", on_accent="#11111b",
    ),
    "light": dict(
        base="#eff1f5", mantle="#e6e9ef", surface="#ccd0da",
        surface2="#bcc0cc", text="#4c4f69", sub="#6c6f85",
        accent="#1e66f5", on_accent="#ffffff",
    ),
}

QSS = """
QWidget {{ font-size: 13px; }}
QTabWidget::pane {{ border: none; background: {base}; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {sub};
    padding: 8px 20px; border: none; margin-right: 4px;
}}
QTabBar::tab:selected {{
    color: {accent}; border-bottom: 2px solid {accent}; font-weight: 600;
}}
QPushButton {{
    background: {surface}; border: none; border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton:hover {{ background: {surface2}; }}
QPushButton:disabled {{ color: {sub}; }}
QPushButton#accent {{
    background: {accent}; color: {on_accent}; font-weight: 600;
}}
QPushButton#accent:hover {{ background: {accent}; }}
QPushButton#ghost {{
    background: transparent; border: 1px solid {surface2};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {mantle}; border: 1px solid {surface};
    border-radius: 8px; padding: 6px 10px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {mantle}; border: 1px solid {surface};
    selection-background-color: {surface};
}}
QGroupBox {{
    border: 1px solid {surface}; border-radius: 10px;
    margin-top: 12px; padding: 10px 4px 4px 4px; color: {sub};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QListWidget {{
    background: {mantle}; border: 1px solid {surface};
    border-radius: 10px; padding: 6px; outline: none;
}}
QListWidget::item {{ padding: 8px; border-radius: 6px; color: {text}; }}
QListWidget::item:hover {{ background: {surface}; }}
QListWidget::item:selected {{ background: {surface}; color: {accent}; }}
QProgressBar {{
    background: {mantle}; border: none; border-radius: 5px;
    max-height: 10px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {surface2}; background: {mantle};
}}
QCheckBox::indicator:checked {{ background: {accent}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{
    background: {surface2}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def apply_theme(app, name):
    p = PALETTES[name]
    pal = QPalette()
    roles = {
        QPalette.Window: "base",
        QPalette.WindowText: "text",
        QPalette.Base: "mantle",
        QPalette.AlternateBase: "surface",
        QPalette.Text: "text",
        QPalette.Button: "surface",
        QPalette.ButtonText: "text",
        QPalette.Highlight: "accent",
        QPalette.HighlightedText: "on_accent",
        QPalette.ToolTipBase: "mantle",
        QPalette.ToolTipText: "text",
        QPalette.PlaceholderText: "sub",
    }
    for role, key in roles.items():
        pal.setColor(role, QColor(p[key]))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(p["sub"]))
    app.setPalette(pal)
    app.setStyleSheet(QSS.format(**p))


# ── KDE-friendly file dialogs ──


def _kdialog(argv):
    """Run kdialog; '' means cancelled, None means kdialog unusable."""
    try:
        r = subprocess.run(
            ["kdialog", *argv], capture_output=True, text=True, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else ""


def pick_open(parent):
    if shutil.which("kdialog"):
        res = _kdialog(
            ["--title", "Open video or image",
             "--getopenfilename", os.path.expanduser("~"), FILE_FILTER]
        )
        if res is not None:
            return res or None
    path, _ = QFileDialog.getOpenFileName(
        parent, "Open video or image", os.path.expanduser("~"),
        FILE_FILTER + ";;All files (*)",
    )
    return path or None


def pick_save(parent, suggested):
    if shutil.which("kdialog"):
        res = _kdialog(
            ["--title", "Export", "--getsavefilename", suggested,
             "GIF or PNG (*.gif *.png)"]
        )
        if res is not None:
            return res or None
    path, _ = QFileDialog.getSaveFileName(
        parent, "Export", suggested, "GIF (*.gif);;PNG (*.png)"
    )
    return path or None


# ── desktop menu entry ──


LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.txt")


def logo_art():
    """The app's ASCII "M" (see the logo credit in the README)."""
    try:
        with open(LOGO_PATH) as f:
            return f.read().rstrip("\n").split("\n")
    except OSError:
        return generators.donut(40, 21, 1)[0]


def logo_reveal_frames(frames=48, hold=24, seed=3):
    """The logo drawing itself: cells appear in a diagonal sweep with a
    dithered frontier, then the finished M holds for a moment."""
    art = logo_art()
    h, w = len(art), max(len(line) for line in art)
    rng = random.Random(seed)
    jitter = [[rng.uniform(0, 14) for _ in range(w)] for _ in range(h)]
    out = []
    for step in range(frames):
        t = (step + 1) / frames * (w + h + 14)
        lines = []
        for y, line in enumerate(art):
            row = []
            for x in range(w):
                ch = line[x] if x < len(line) else " "
                row.append(ch if x + y + jitter[y][x] < t else " ")
            lines.append("".join(row))
        out.append(lines)
    out.extend([list(art)] * hold)
    return out, w, h


def _make_icon(path):
    art = logo_art()
    rows, cols = len(art), max(len(line) for line in art)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 248, 248), radius=52, fill=(30, 30, 46, 255))
    font = export.find_font(13)
    step_x, step_y = 212 / cols, 200 / rows
    for y, line in enumerate(art):
        for x, ch in enumerate(line):
            if ch != " ":
                draw.text(
                    (24 + x * step_x, 30 + y * step_y), ch,
                    font=font, fill=(137, 180, 250, 255),
                )
    img.save(path)


def ensure_desktop_entry():
    """Install a menu launcher (and icon) on first run; refresh if stale."""
    try:
        apps = os.path.expanduser("~/.local/share/applications")
        icons = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
        os.makedirs(apps, exist_ok=True)
        os.makedirs(icons, exist_ok=True)
        icon_path = os.path.join(icons, "motionfetch.png")
        _make_icon(icon_path)  # cheap; keeps the icon current across updates

        exe = shutil.which("motionfetch-gui")
        if exe:
            exec_line = exe
        else:
            exe = shutil.which("motionfetch")
            exec_line = f"{exe} gui" if exe else \
                f"{sys.executable} -m motionfetch gui"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=motionfetch\n"
            "Comment=Turn videos and images into terminal animations\n"
            f"Exec={exec_line}\n"
            f"Icon={icon_path}\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Graphics;Utility;\n"
        )
        entry = os.path.join(apps, "motionfetch.desktop")
        try:
            with open(entry) as f:
                if f.read() == content:
                    return icon_path
        except OSError:
            pass
        with open(entry, "w") as f:
            f.write(content)
        return icon_path
    except OSError:
        return None


# ── shared helpers ──


def pil_to_pixmap(img):
    img = img.convert("RGB")
    data = img.tobytes()
    qimg = QImage(
        data, img.width, img.height, img.width * 3, QImage.Format_RGB888
    ).copy()
    return QPixmap.fromImage(qimg)


def grab_source_frame(path):
    """First look at the source: a PIL image to crop against."""
    if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
        for seek in (["-ss", "1"], []):
            out = subprocess.run(
                ["ffmpeg", "-v", "error", *seek, "-i", path, "-frames:v", "1",
                 "-f", "image2pipe", "-c:v", "png", "pipe:1"],
                capture_output=True,
            ).stdout
            if out:
                return Image.open(io.BytesIO(out)).convert("RGB")
        raise convert.ConvertError(f"could not read a frame from {path!r}")
    return Image.open(path).convert("RGB")


def build_meta(frames, params):
    style = params["style"]
    if style == "image":
        first = frames[0]
        return {
            "mode": "image", "style": style, "fps": params["fps"],
            "width": params["width"],
            "height": max(
                1, round(params["width"] * first.height / first.width * 0.5)
            ),
            "source": os.path.basename(params["path"]),
        }
    return {
        "mode": "color" if style in ("blocks", "ascii-color") else "mono",
        "style": style,
        "fps": params["fps"],
        "width": params["width"],
        "height": len(frames[0]),
        "source": os.path.basename(params["path"]),
    }


class CropView(QWidget):
    """Shows the source frame; drag to draw a crop box, drag inside to move
    it, drag a corner to resize. `crop_dict()` feeds convert.crop_box."""

    changed = Signal()

    HANDLE = 12

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 320)
        self._pix = None
        self._src = QSize(1, 1)
        self._rect = None          # QRect in source pixels, or None = all
        self._mode = None
        self._anchor = QPoint()
        self.setCursor(Qt.CrossCursor)

    def set_image(self, pil_img):
        self._src = QSize(pil_img.width, pil_img.height)
        self._pix = pil_to_pixmap(pil_img)
        self._rect = None
        self.update()
        self.changed.emit()

    def clear_crop(self):
        self._rect = None
        self.update()
        self.changed.emit()

    def crop_dict(self):
        if self._rect is None:
            return {}
        r = self._rect
        return {
            "top": str(r.top()),
            "left": str(r.left()),
            "bottom": str(self._src.height() - r.bottom() - 1),
            "right": str(self._src.width() - r.right() - 1),
        }

    def describe(self):
        if self._rect is None:
            return "no crop — drag on the image to select an area"
        r = self._rect
        return f"crop: {r.width()}x{r.height()} at ({r.left()}, {r.top()})"

    # geometry between widget and source coordinates
    def _view(self):
        if not self._pix:
            return 1.0, 0, 0
        scale = min(
            self.width() / self._src.width(), self.height() / self._src.height()
        )
        ox = (self.width() - self._src.width() * scale) / 2
        oy = (self.height() - self._src.height() * scale) / 2
        return scale, ox, oy

    def _to_src(self, pos):
        scale, ox, oy = self._view()
        x = round((pos.x() - ox) / scale)
        y = round((pos.y() - oy) / scale)
        return QPoint(
            max(0, min(x, self._src.width() - 1)),
            max(0, min(y, self._src.height() - 1)),
        )

    def _to_widget(self, rect):
        scale, ox, oy = self._view()
        return QRect(
            int(rect.left() * scale + ox),
            int(rect.top() * scale + oy),
            int(rect.width() * scale),
            int(rect.height() * scale),
        )

    def _corner_at(self, pos):
        if self._rect is None:
            return None
        wr = self._to_widget(self._rect)
        corners = {
            "tl": wr.topLeft(), "tr": wr.topRight(),
            "bl": wr.bottomLeft(), "br": wr.bottomRight(),
        }
        for name, pt in corners.items():
            if (pos - pt).manhattanLength() <= self.HANDLE:
                return name
        return None

    def mousePressEvent(self, ev):
        if not self._pix:
            return
        corner = self._corner_at(ev.position().toPoint())
        src = self._to_src(ev.position().toPoint())
        if corner:
            self._mode = corner
        elif self._rect and self._rect.contains(src):
            self._mode = "move"
            self._anchor = src - self._rect.topLeft()
        else:
            self._mode = "new"
            self._rect = QRect(src, src)
        self.update()

    def mouseMoveEvent(self, ev):
        if not self._pix or self._mode is None:
            return
        src = self._to_src(ev.position().toPoint())
        r = self._rect
        if self._mode == "new":
            self._rect = QRect(r.topLeft(), src)
        elif self._mode == "move":
            tl = src - self._anchor
            tl.setX(max(0, min(tl.x(), self._src.width() - r.width())))
            tl.setY(max(0, min(tl.y(), self._src.height() - r.height())))
            self._rect = QRect(tl, r.size())
        elif self._mode == "tl":
            self._rect = QRect(src, r.bottomRight())
        elif self._mode == "tr":
            self._rect = QRect(
                QPoint(r.left(), src.y()), QPoint(src.x(), r.bottom())
            )
        elif self._mode == "bl":
            self._rect = QRect(
                QPoint(src.x(), r.top()), QPoint(r.right(), src.y())
            )
        elif self._mode == "br":
            self._rect = QRect(r.topLeft(), src)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _ev):
        if self._rect is not None:
            self._rect = self._rect.normalized()
            if self._rect.width() < 8 or self._rect.height() < 8:
                self._rect = None
        self._mode = None
        self.update()
        self.changed.emit()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(17, 17, 27))
        p.drawRoundedRect(self.rect(), 10, 10)
        if not self._pix:
            p.setPen(QColor(140, 140, 160))
            p.drawText(self.rect(), Qt.AlignCenter, "open a video or image")
            return
        scale, ox, oy = self._view()
        target = QRect(
            int(ox), int(oy),
            int(self._src.width() * scale), int(self._src.height() * scale),
        )
        p.drawPixmap(target, self._pix)
        if self._rect is not None:
            wr = self._to_widget(self._rect)
            shade = QColor(0, 0, 0, 140)
            p.setPen(Qt.NoPen)
            p.setBrush(shade)
            for outside in (
                QRect(target.left(), target.top(), target.width(),
                      wr.top() - target.top()),
                QRect(target.left(), wr.bottom(), target.width(),
                      target.bottom() - wr.bottom()),
                QRect(target.left(), wr.top(), wr.left() - target.left(),
                      wr.height()),
                QRect(wr.right(), wr.top(), target.right() - wr.right(),
                      wr.height()),
            ):
                p.drawRect(outside)
            p.setPen(QPen(QColor(137, 180, 250), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(wr)
            p.setBrush(QColor(137, 180, 250))
            for pt in (wr.topLeft(), wr.topRight(), wr.bottomLeft(),
                       wr.bottomRight()):
                p.drawRect(QRect(pt - QPoint(4, 4), QSize(8, 8)))


class ConvertWorker(QThread):
    progress = Signal(int)
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.p = params

    def run(self):
        p = self.p
        try:
            if p["is_video"]:
                frames = []
                gen = convert.video_frames(
                    p["path"], p["width"], p["style"], p["fps"], p["crop"],
                    start=p["start"], duration=p["duration"],
                    max_frames=p["max_frames"], gamma=p["gamma"],
                    invert=p["invert"],
                )
                for frame in gen:
                    frames.append(frame)
                    self.progress.emit(len(frames))
                    time.sleep(0.002)  # keep the UI responsive (GIL)
            else:
                frames = convert.image_frames(
                    p["path"], p["width"], p["style"], p["crop"],
                    gamma=p["gamma"], invert=p["invert"],
                )
            if not frames:
                raise convert.ConvertError("conversion produced no frames")
            convert.pad_frames(frames, p["width"])
            self.done.emit(frames)
        except Exception as e:  # surfaced in the UI
            self.failed.emit(str(e))


class RenderWorker(QThread):
    """Renders preview frames to QImages off the UI thread — drawing a few
    dozen frames glyph by glyph is exactly the kind of work that used to
    freeze the window. QImage is thread-safe to build; only the QPixmap
    conversion happens back on the UI side."""

    frame_ready = Signal(int, QImage)

    def __init__(self, token, meta, frames, tint, font_size):
        super().__init__()
        self.token = token
        self.meta = meta
        self.frames = frames
        self.tint = tint
        self.font_size = font_size

    def run(self):
        meta = self.meta
        try:
            if meta.get("mode") == "image":
                for f in self.frames:
                    qimg = QImage()
                    if isinstance(f, (bytes, bytearray)):
                        qimg.loadFromData(bytes(f))
                    else:
                        rgb = f.convert("RGB")
                        qimg = QImage(
                            rgb.tobytes(), rgb.width, rgb.height,
                            rgb.width * 3, QImage.Format_RGB888,
                        ).copy()
                    self.frame_ready.emit(
                        self.token,
                        qimg.scaled(560, 380, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation),
                    )
                    time.sleep(0.001)  # let the UI breathe (GIL)
            else:
                font = export.find_font(
                    self.font_size, braille=meta.get("style") == "braille"
                )
                cw = max(round(font.getlength("█")), 1)
                ch = max(font.getbbox("█")[3], 1)
                for f in self.frames:
                    pil = export.render_frame_image(
                        f, meta, font, cw, ch, self.tint, chrome=False
                    ).convert("RGB")
                    self.frame_ready.emit(
                        self.token,
                        QImage(
                            pil.tobytes(), pil.width, pil.height,
                            pil.width * 3, QImage.Format_RGB888,
                        ).copy(),
                    )
                    time.sleep(0.001)
        except Exception:
            pass  # a broken preview should never take the window down


class AnimationView(QLabel):
    """Plays a converted animation. Rendering happens in a worker thread;
    while it runs the label just says so instead of blocking the window."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(240)
        self._pixmaps = []
        self._i = 0
        self._fps = 15
        self._token = 0
        self._workers = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def show_frames(self, meta, frames, tint=None, font_size=12, limit=48):
        self._token += 1
        self._timer.stop()
        self._pixmaps = []
        self._fps = max(meta.get("fps", 15), 1)
        self.setText("rendering preview…")
        worker = RenderWorker(
            self._token, meta, frames[:limit], tint, font_size
        )
        worker.frame_ready.connect(self._on_frame)
        worker.finished.connect(
            lambda w=worker: self._workers.remove(w)
            if w in self._workers else None
        )
        self._workers.append(worker)
        worker.start()

    def _on_frame(self, token, image):
        # frames stream in as they render; the loop starts on the first one
        if token != self._token:
            return
        self._pixmaps.append(QPixmap.fromImage(image))
        if len(self._pixmaps) == 1:
            self._i = 0
            self.setPixmap(self._pixmaps[0])
            self._timer.start(int(1000 / self._fps))

    def stop(self):
        self._token += 1
        self._timer.stop()
        self._pixmaps = []
        self.clear()

    def shutdown(self):
        """Block until render workers finish (called on window close —
        destroying a QThread mid-run aborts the process)."""
        self._token += 1
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(3000)

    def _tick(self):
        if not self._pixmaps:
            return
        self._i = (self._i + 1) % len(self._pixmaps)
        self.setPixmap(self._pixmaps[self._i])


class ConvertTab(QWidget):
    saved = Signal(str)

    def __init__(self):
        super().__init__()
        self.path = None
        self.worker = None

        open_btn = QPushButton("Open video or image…")
        open_btn.setObjectName("accent")
        open_btn.clicked.connect(self.open_file)
        self.path_label = QLabel("nothing open yet")

        self.crop_view = CropView()
        self.crop_label = QLabel()
        clear_crop = QPushButton("Clear crop")
        clear_crop.setObjectName("ghost")
        clear_crop.clicked.connect(self.crop_view.clear_crop)
        self.crop_view.changed.connect(
            lambda: self.crop_label.setText(self.crop_view.describe())
        )
        self.crop_label.setText(self.crop_view.describe())

        self.name_edit = QLineEdit()
        self.style_box = QComboBox()
        self.style_box.addItems(convert.STYLES)
        self.style_box.setToolTip(
            "blocks: color pixels from characters\n"
            "ascii / braille: text, tinted with your theme\n"
            "image: the real untouched pixels (kitty terminal)"
        )
        self.width_spin = QSpinBox(minimum=16, maximum=200, value=48)
        self.fps_spin = QSpinBox(minimum=1, maximum=60, value=15)
        self.start_edit = QLineEdit(placeholderText="e.g. 12 or 0:30")
        self.dur_edit = QLineEdit(placeholderText="whole video")
        self.frames_spin = QSpinBox(minimum=1, maximum=2000, value=400)
        self.gamma_spin = QDoubleSpinBox(
            minimum=0.2, maximum=3.0, value=1.0, singleStep=0.1
        )
        self.invert_check = QCheckBox("invert (ascii/braille)")

        form_box = QGroupBox("Conversion")
        form = QFormLayout(form_box)
        form.addRow("name", self.name_edit)
        form.addRow("style", self.style_box)
        form.addRow("width (cols)", self.width_spin)
        form.addRow("fps", self.fps_spin)
        form.addRow("start (s)", self.start_edit)
        form.addRow("duration (s)", self.dur_edit)
        form.addRow("max frames", self.frames_spin)
        form.addRow("gamma", self.gamma_spin)
        form.addRow("", self.invert_check)

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(lambda: self.convert(preview=True))
        self.save_btn = QPushButton("Convert && save")
        self.save_btn.setObjectName("accent")
        self.save_btn.clicked.connect(lambda: self.convert(preview=False))
        self.progress = QProgressBar()
        self.progress.hide()
        self.anim_view = AnimationView()

        left = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(open_btn)
        row.addWidget(self.path_label, 1)
        left.addLayout(row)
        left.addWidget(self.crop_view, 1)
        crop_row = QHBoxLayout()
        crop_row.addWidget(self.crop_label, 1)
        crop_row.addWidget(clear_crop)
        left.addLayout(crop_row)

        right = QVBoxLayout()
        right.addWidget(form_box)
        btns = QHBoxLayout()
        btns.addWidget(self.preview_btn)
        btns.addWidget(self.save_btn)
        right.addLayout(btns)
        right.addWidget(self.progress)
        right.addWidget(self.anim_view, 1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)
        lay.addLayout(left, 3)
        lay.addLayout(right, 2)

        self.show_logo_intro()

    def show_logo_intro(self):
        """The M drawing itself in ascii, until a real preview replaces it."""
        frames, w, h = logo_reveal_frames()
        meta = {"mode": "mono", "style": "ascii", "fps": 30,
                "width": w, "height": h}
        self.anim_view.show_frames(meta, frames, font_size=11, limit=100)

    def open_file(self):
        path = pick_open(self)
        if not path:
            return
        try:
            frame = grab_source_frame(path)
        except Exception as e:
            QMessageBox.warning(self, "motionfetch", str(e))
            return
        self.path = path
        self.path_label.setText(os.path.basename(path))
        self.crop_view.set_image(frame)
        base = os.path.splitext(os.path.basename(path))[0]
        self.name_edit.setText(
            "".join(c if c.isalnum() or c in "-_" else "-" for c in base)
        )

    def params(self, preview):
        ext = os.path.splitext(self.path)[1].lower()
        return {
            "path": self.path,
            "is_video": ext in VIDEO_EXTS,
            "width": self.width_spin.value(),
            "style": self.style_box.currentText(),
            "fps": self.fps_spin.value(),
            "crop": self.crop_view.crop_dict(),
            "start": self.start_edit.text().strip() or None,
            "duration": self.dur_edit.text().strip() or None,
            "max_frames": min(64, self.frames_spin.value())
            if preview else self.frames_spin.value(),
            "gamma": self.gamma_spin.value(),
            "invert": self.invert_check.isChecked(),
        }

    def convert(self, preview):
        if not self.path:
            QMessageBox.information(
                self, "motionfetch", "Open a video or image first."
            )
            return
        if self.worker and self.worker.isRunning():
            return
        params = self.params(preview)
        self.progress.setRange(0, params["max_frames"])
        self.progress.setValue(0)
        self.progress.show()
        self.preview_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.worker = ConvertWorker(params)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.failed.connect(self.on_failed)
        self.worker.done.connect(
            self.on_preview if preview else self.on_save_ready
        )
        self.worker.start()

    def finish_worker(self):
        self.progress.hide()
        self.preview_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

    def on_failed(self, msg):
        self.finish_worker()
        QMessageBox.warning(self, "motionfetch", msg)

    def on_preview(self, frames):
        self.finish_worker()
        self.anim_view.show_frames(build_meta(frames, self.worker.p), frames)

    def on_save_ready(self, frames):
        self.finish_worker()
        params = self.worker.p
        name = self.name_edit.text().strip()
        try:
            library.check_name(name)
        except library.LibraryError as e:
            QMessageBox.warning(self, "motionfetch", str(e))
            return
        if library.exists(name):
            answer = QMessageBox.question(
                self, "motionfetch", f"'{name}' already exists — overwrite?"
            )
            if answer != QMessageBox.Yes:
                return
        meta = build_meta(frames, params)
        library.save(name, frames, meta, overwrite=True)
        self.anim_view.show_frames(meta, frames)
        self.saved.emit(name)


class LibraryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)
        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.anim_view = AnimationView()

        gen_btn = QPushButton("Generate built-in…")
        gen_btn.clicked.connect(self.generate)
        link_btn = QPushButton("Create command")
        link_btn.setObjectName("accent")
        link_btn.clicked.connect(self.make_link)
        self.copy_btn = QPushButton("Copy command")
        self.copy_btn.clicked.connect(self.copy_command)
        export_btn = QPushButton("Export GIF/PNG")
        export_btn.clicked.connect(self.export_anim)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("ghost")
        del_btn.clicked.connect(self.delete)

        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addWidget(gen_btn)

        right = QVBoxLayout()
        right.addWidget(self.anim_view, 1)
        right.addWidget(self.meta_label)
        btns = QHBoxLayout()
        for b in (link_btn, self.copy_btn, export_btn, del_btn):
            btns.addWidget(b)
        right.addLayout(btns)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)
        lay.addLayout(left, 1)
        lay.addLayout(right, 2)
        self.refresh()

    def current_name(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh(self, select=None):
        self.list.clear()
        installed = links.installed()
        for meta in library.list_all():
            name = meta["name"]
            label = f"{name}   {meta['width']}x{meta['height']}, " \
                    f"{meta['frames']} frames"
            if name in installed:
                label += f"   → {installed[name]}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
            if name == select:
                self.list.setCurrentItem(item)
        if select is None and self.list.count():
            self.list.setCurrentRow(0)

    def on_select(self, item, _prev=None):
        if not item:
            self.anim_view.stop()
            self.meta_label.clear()
            return
        name = item.data(Qt.UserRole)
        try:
            meta, frames = library.load(name)
        except library.LibraryError as e:
            self.meta_label.setText(str(e))
            return
        self.anim_view.show_frames(meta, frames, font_size=10)
        note = ""
        if meta.get("mode") == "image":
            note = " (needs a kitty-protocol terminal)"
        self.meta_label.setText(
            f"<b>{name}</b> — {meta.get('style')}, {meta['frames']} frames, "
            f"{meta['width']}x{meta['height']} cells, {meta['fps']} fps, "
            f"from {meta.get('source')}<br>"
            f"terminal: <code>motionfetch fetch {name}</code>{note}"
        )

    def generate(self):
        kinds = sorted(generators.GENERATORS)
        kind, ok = QInputDialog.getItem(
            self, "motionfetch", "animation:", kinds, editable=False
        )
        if not ok:
            return
        func, mode = generators.GENERATORS[kind]
        frames = func(48, 20, 200)
        convert.pad_frames(frames, 48)
        meta = {
            "mode": mode, "style": "generated", "fps": 15,
            "width": 48, "height": 20, "source": f"builtin:{kind}",
        }
        library.save(kind, frames, meta, overwrite=True)
        self.refresh(select=kind)

    def make_link(self):
        name = self.current_name()
        if not name:
            return
        command, ok = QInputDialog.getText(
            self, "motionfetch",
            "command name (installed in ~/.local/bin):",
            text=f"fastfetch-{name}",
        )
        if not ok or not command.strip():
            return
        try:
            path = links.create(name, command.strip(), force=True)
        except links.LinkError as e:
            QMessageBox.warning(self, "motionfetch", str(e))
            return
        QMessageBox.information(
            self, "motionfetch",
            f"Created {command.strip()} — run it from any terminal.\n({path})",
        )
        self.refresh(select=name)

    def copy_command(self):
        name = self.current_name()
        if not name:
            return
        installed = links.installed()
        cmd = installed.get(name, f"motionfetch fetch {name}")
        QApplication.clipboard().setText(cmd)
        self.copy_btn.setText("Copied!")
        QTimer.singleShot(
            1500, lambda: self.copy_btn.setText("Copy command")
        )

    def export_anim(self):
        name = self.current_name()
        if not name:
            return
        out = pick_save(self, os.path.expanduser(f"~/{name}.gif"))
        if not out:
            return
        meta, frames = library.load(name)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            export.export(meta, frames, out)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "motionfetch", str(e))
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "motionfetch", f"Wrote {out}")

    def delete(self):
        name = self.current_name()
        if not name:
            return
        answer = QMessageBox.question(
            self, "motionfetch", f"Delete '{name}'?"
        )
        if answer != QMessageBox.Yes:
            return
        self.anim_view.stop()
        library.remove(name)
        self.refresh()


class MainWindow(QMainWindow):
    def __init__(self, app, settings):
        super().__init__()
        self.app = app
        self.settings = settings
        self.setWindowTitle("motionfetch")
        self.resize(1100, 660)

        tabs = QTabWidget()
        self.convert_tab = ConvertTab()
        self.library_tab = LibraryTab()
        tabs.addTab(self.convert_tab, "Convert")
        tabs.addTab(self.library_tab, "Library")
        tabs.setDocumentMode(True)
        self.setCentralWidget(tabs)
        self.convert_tab.saved.connect(self.on_saved)
        self.tabs = tabs

        self.theme = settings.value("theme", "dark")
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ghost")
        self.theme_btn.setFixedWidth(44)
        self.theme_btn.setToolTip("dark / light")
        self.theme_btn.clicked.connect(self.toggle_theme)
        tabs.setCornerWidget(self.theme_btn, Qt.TopRightCorner)
        self._set_theme(self.theme)

    def _set_theme(self, name):
        self.theme = name
        apply_theme(self.app, name)
        self.theme_btn.setText("☀" if name == "dark" else "☾")
        self.settings.setValue("theme", name)

    def toggle_theme(self):
        self._set_theme("light" if self.theme == "dark" else "dark")

    def on_saved(self, name):
        self.library_tab.refresh(select=name)
        self.tabs.setCurrentWidget(self.library_tab)

    def closeEvent(self, event):
        self.convert_tab.anim_view.shutdown()
        self.library_tab.anim_view.shutdown()
        worker = self.convert_tab.worker
        if worker and worker.isRunning():
            worker.wait(5000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("motionfetch")
    app.setStyle("Fusion")
    settings = QSettings("motionfetch", "motionfetch")
    icon_path = ensure_desktop_entry()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow(app, settings)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
