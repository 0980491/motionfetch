"""Qt interface: convert with a draggable crop box and a live preview,
then manage the library (play, link commands, export, delete).

Needs PySide6 (`pipx install 'motionfetch[gui]'`). Everything the GUI does
goes through the same modules as the CLI, so both stay in sync.
"""

import io
import os
import subprocess
import sys

from PIL import Image
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import convert, export, generators, library, links, player

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


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
        p.fillRect(self.rect(), QColor(24, 24, 34))
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
            p.setClipRegion(self.rect())
            shade = QColor(0, 0, 0, 140)
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
                p.fillRect(outside, shade)
            pen = QPen(QColor(137, 180, 250), 2)
            p.setPen(pen)
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


class AnimationView(QLabel):
    """Plays a converted animation, rendered with the export engine."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(240)
        self._pixmaps = []
        self._i = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def show_frames(self, meta, frames, tint=None, font_size=12, limit=64):
        font = export.find_font(font_size)
        box = font.getbbox("█")
        cw, ch = max(box[2] - box[0], 1), max(box[3], 1)
        self._pixmaps = [
            pil_to_pixmap(
                export.render_frame_image(f, meta, font, cw, ch, tint)
            )
            for f in frames[:limit]
        ]
        self._i = 0
        if self._pixmaps:
            self.setPixmap(self._pixmaps[0])
            self._timer.start(int(1000 / max(meta.get("fps", 15), 1)))

    def stop(self):
        self._timer.stop()
        self._pixmaps = []
        self.clear()

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
        open_btn.clicked.connect(self.open_file)
        self.path_label = QLabel("nothing open yet")
        self.path_label.setStyleSheet("color: #888")

        self.crop_view = CropView()
        self.crop_label = QLabel()
        clear_crop = QPushButton("Clear crop")
        clear_crop.clicked.connect(self.crop_view.clear_crop)
        self.crop_view.changed.connect(
            lambda: self.crop_label.setText(self.crop_view.describe())
        )
        self.crop_label.setText(self.crop_view.describe())

        self.name_edit = QLineEdit()
        self.style_box = QComboBox()
        self.style_box.addItems(convert.STYLES)
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
        lay.addLayout(left, 3)
        lay.addLayout(right, 2)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video or image", os.path.expanduser("~"),
            "Videos and images (*.mp4 *.mkv *.webm *.mov *.avi *.m4v "
            "*.gif *.png *.jpg *.jpeg *.webp);;All files (*)",
        )
        if not path:
            return
        try:
            frame = grab_source_frame(path)
        except Exception as e:
            QMessageBox.warning(self, "motionfetch", str(e))
            return
        self.path = path
        self.path_label.setText(os.path.basename(path))
        self.path_label.setStyleSheet("")
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

    def meta_for(self, frames, params):
        return {
            "mode": "color"
            if params["style"] in ("blocks", "ascii-color") else "mono",
            "style": params["style"],
            "fps": params["fps"],
            "width": params["width"],
            "height": len(frames[0]),
            "source": os.path.basename(params["path"]),
        }

    def on_preview(self, frames):
        self.finish_worker()
        self.anim_view.show_frames(self.meta_for(frames, self.worker.p), frames)

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
        meta = self.meta_for(frames, params)
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
        link_btn.clicked.connect(self.make_link)
        export_btn = QPushButton("Export GIF/PNG")
        export_btn.clicked.connect(self.export_anim)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete)

        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addWidget(gen_btn)

        right = QVBoxLayout()
        right.addWidget(self.anim_view, 1)
        right.addWidget(self.meta_label)
        btns = QHBoxLayout()
        for b in (link_btn, export_btn, del_btn):
            btns.addWidget(b)
        right.addLayout(btns)

        lay = QHBoxLayout(self)
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
        self.meta_label.setText(
            f"<b>{name}</b> — {meta.get('style')}, {meta['frames']} frames, "
            f"{meta['width']}x{meta['height']} cells, {meta['fps']} fps, "
            f"from {meta.get('source')}<br>"
            f"terminal: <code>motionfetch fetch {name}</code>"
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

    def export_anim(self):
        name = self.current_name()
        if not name:
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Export", os.path.expanduser(f"~/{name}.gif"),
            "GIF (*.gif);;PNG (*.png)",
        )
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("motionfetch")
        self.resize(1100, 640)
        tabs = QTabWidget()
        self.convert_tab = ConvertTab()
        self.library_tab = LibraryTab()
        tabs.addTab(self.convert_tab, "Convert")
        tabs.addTab(self.library_tab, "Library")
        self.setCentralWidget(tabs)
        self.convert_tab.saved.connect(self.on_saved)
        self.tabs = tabs

    def on_saved(self, name):
        self.library_tab.refresh(select=name)
        self.tabs.setCurrentWidget(self.library_tab)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("motionfetch")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
