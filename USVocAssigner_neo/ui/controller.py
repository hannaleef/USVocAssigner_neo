# [ui/controller.py] 5단계 통합 GUI
# 1) N채널 WAV 입력 -> Ratio-only 분석
# 2) 채널별 스펙트로그램 + QMediaPlayer 영상 재생
# 3) SLEAP CSV 로드 -> 추적 ID 확인(Anchor) -> 오디오/비디오 offset
# 4) 실측 geometry.json 입력이 있을 때만 거리/각도 후보별 점수 계산
# 5) 결과/채널 레벨/불확실 사유 CSV export
#
# 기존 Qt Designer의 Ui_MainWindow, tab_audio, tab_video 및 objectName 재사용.
# ui_mainwindow.py는 직접 변경하지 않는다.
# GUI에서 실행되는 계산량 큰 STFT·할당은 Qt thread pool에서 처리한다.
# Audio-only: 원본 1초=실제 재생 1초. 48kHz polyphase resampling으로 길이 보존.
# 초음파용 heterodyne은 주파수만 가청 대역으로 이동하며 시간축/분석 WAV는 보존.
# Play All: 비디오와 원본 시간축의 스펙트로그램 시각화만 동기 진행 (가청 preview 동시 출력 아님).
# 제한: 특정 SLEAP version CLI tracking은 track_run.py에서 별도 실행.

import csv
import json
import tempfile
import wave
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
import pandas as pd
import pyqtgraph as pg

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QRectF, QEvent
from PySide6.QtCore import QSignalBlocker, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QColor, QPainter, QPen, QPdfWriter, QPageSize, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QInputDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
    QSizePolicy, QFrame, QGroupBox, QLayout, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QStyle, QStyleOptionSlider
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from .ui_mainwindow import Ui_MainWindow
from audio.data_load import (
    load_audio, load_labels, inspect_delimited_file, read_delimited_table
)
from audio.voc_label import merge_channel_events
from audio.aud_process import window_spectrogram
from video.track_data import process_tracking_data
from video.track_anchor import (
    select_anchor_frame, draw_anchor_frame, save_anchor_mapping,
    validate_audio_video_mapping
)
from voc_assign.aud_vid_sync import SyncConfig, sync_from_start_points
from voc_assign.ratio_corr import GeometryConfig, EmpiricalGeoConfig, EmpiricalThreshold
from voc_assign.voc_assign import run_assignment, run_empirical_assignment
from executor.result_viewer import format_results, export_results_csv, result_records


# Scientific intensity colormap: stronger log-magnitude -> strictly brighter.
# This is a display colormap for relative STFT levels, NOT measured SPL.
# Spectrogram canvas, surrounding channel frame, and main dark UI share
# exactly the same background color. No bright/dark rectangle around the plot.
# Data at the lowest fixed dB level also maps to this RGB value.
SPECTROGRAM_BG = "#1E1E1E"
SPECTROGRAM_AXIS = "#B8C8D1"
SPECTROGRAM_AXIS_LINE = "#657985"
SPECTROGRAM_PLAYHEAD = "#FFB85C"
SPECTROGRAM_SYNC_MARKER = "#40C9DF"

# Lowest signal = canvas #1E1E1E. Stronger signal -> increasingly bright cyan.
# The fixed dB range (-100 to 0 by default) never auto-adjusts on scroll.
SPECTROGRAM_COLOR_STOPS = [
    (0.00, (30, 30, 30, 255)),     # #1E1E1E, same as canvas and empty space
    (0.16, (38, 56, 65, 255)),     # #263841, 아주 약한 신호
    (0.35, (28, 91, 112, 255)),    # #1C5B70, 청록 전환
    (0.55, (35, 146, 171, 255)),   # #2392AB, 중간 세기
    (0.76, (64, 201, 223, 255)),   # #40C9DF, UI 포인트와 같은 시안
    (0.91, (169, 237, 235, 255)),  # #A9EDEB, 밝은 민트
    (1.00, (255, 250, 220, 255)),  # #FFFADC, 가장 강한 신호
]


def spectrogram_lookup_table():
    """Create 256-entry pyqtgraph RGBA LUT for increasing signal strength."""
    positions = np.array([stop[0] for stop in SPECTROGRAM_COLOR_STOPS])
    colors = np.array([stop[1] for stop in SPECTROGRAM_COLOR_STOPS])
    return pg.ColorMap(positions, colors).getLookupTable(nPts=256, mode="byte")


class TimelineStartMarker(QWidget):
    """기존 QSlider 위에 투명하게 겹치는 '고정 시작점' 세로선.

    QSlider를 수정하거나 재생 핸들의 위치를 이동시키지 않는다.
    marker_ms는 독립적으로 저장하므로 사용자가 탐색해도 기준점은 고정된다.
    슬라이더 너비/범위가 변하면 Qt 스타일을 기준으로 표시 위치를 재계산한다.
    """

    def __init__(self, slider, color="#21CB86"):
        super().__init__(slider)
        self.slider = slider
        self.marker_ms = None  # 아직 기준점이 없으면 None
        self.marker_color = QColor(color)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(slider.rect())
        # Qt 자체 레이아웃/스타일 변화에도 오버레이 크기와 표시를 따라가게 한다.
        slider.installEventFilter(self)
        slider.rangeChanged.connect(lambda minimum, maximum: self.update())
        self.show()
        self.raise_()

    def set_marker_ms(self, marker_ms):
        """고정한 매체 시각(ms)을 설정하거나 None으로 마커를 숨긴다."""
        self.marker_ms = None if marker_ms is None else int(round(marker_ms))
        self.update()

    def eventFilter(self, watched, event):
        if watched is self.slider and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.StyleChange
        ):
            self.setGeometry(self.slider.rect())
            self.raise_()
            self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if self.marker_ms is None or self.slider.orientation() != Qt.Orientation.Horizontal:
            return

        marker_value = max(self.slider.minimum(),
                           min(self.slider.maximum(), self.marker_ms))
        # Qt 스타일이 실제 슬라이더 핸들을 어디에 그리는지 사용하여
        # 좌우 여백/테마에 관계없이 같은 시간의 정확한 x 위치를 구한다.
        option = QStyleOptionSlider()
        option.initFrom(self.slider)
        option.rect = self.slider.rect()
        option.orientation = self.slider.orientation()
        option.minimum = self.slider.minimum()
        option.maximum = self.slider.maximum()
        option.sliderPosition = marker_value
        option.sliderValue = marker_value
        option.singleStep = self.slider.singleStep()
        option.pageStep = self.slider.pageStep()
        option.tickPosition = self.slider.tickPosition()
        option.tickInterval = self.slider.tickInterval()
        rtl = self.slider.layoutDirection() == Qt.LayoutDirection.RightToLeft
        option.upsideDown = bool(self.slider.invertedAppearance()) != rtl

        style = self.slider.style()
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self.slider
        )
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderGroove, self.slider
        )
        x = int(handle.center().x())
        y = int(groove.center().y())
        top = max(1, y - 12)
        bottom = min(self.height() - 2, y + 12)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 밝은 세로선 + 어두운 테두리로 밝은/어두운 테마 모두에서 식별 가능.
        painter.setPen(QPen(QColor("#14212C"), 5))
        painter.drawLine(x, top, x, bottom)
        painter.setPen(QPen(self.marker_color, 3))
        painter.drawLine(x, top, x, bottom)
        painter.end()


class WorkerSignals(QObject):
    """QRunnable에서 main GUI thread로 안전하게 완료 결과를 전달."""
    finished = Signal(int, object, str)


class ComputeJob(QRunnable):
    """result = function(*args) 계산; 예외는 문자열 형태로 UI에 전달."""

    def __init__(self, request_id, function, *args):
        super().__init__()
        self.request_id = request_id
        self.function = function
        self.args = args
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            result = self.function(*self.args)
            self.signals.finished.emit(self.request_id, result, "")
        except Exception as error:
            self.signals.finished.emit(self.request_id, None, str(error))


def _json_safe(value):
    """분석 결과의 NaN/Inf를 JSON null로 변환해 세션 저장 실패를 방지한다."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def compute_spectrograms(audio_slots, start_s, width_s, n_fft, hop_length):
    """스레드에서 모든 채널의 동일 시간 구간을 처리."""
    result = []
    for audio in audio_slots:
        if audio is None:
            result.append(None)
            continue
        data = window_spectrogram(
            audio, start_s, width_s,
            n_fft=n_fft, hop_length=hop_length,
            fmin=18000, fmax=min(100000, audio.sr / 2),
            max_time_bins=1200
        )
        result.append(data)
    return result


def load_geometry_json(path):
    """검증된 카메라/마이크 보정값을 파일에서 읽는다. 임의 기본좌표는 사용하지 않는다.

    geometry.json 예시(숫자는 반드시 실제 측정값으로 교체):
      {"mic_positions_m":{"ch1":[x1,y1],"ch2":[x2,y2]},
       "pixel_to_meter_h":[[h00,h01,h02],[h10,h11,h12],[h20,h21,h22]],
       "recording_bias_db":{"ch1":bias1,"ch2":bias2},
       "back_loss_db":0.0,"max_error_db":12.0,"min_gap_db":2.0}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return GeometryConfig(
        mic_positions_m=data.get("mic_positions_m", {}),
        pixel_to_meter_h=np.asarray(data["pixel_to_meter_h"], dtype=float),
        recording_bias_db=data.get("recording_bias_db"),
        back_loss_db=float(data.get("back_loss_db", 0)),
        max_error_db=float(data.get("max_error_db", 12)),
        min_gap_db=float(data.get("min_gap_db", 2)),
    )


class CSVHeaderDialog(QDialog):
    """CSV/TXT를 읽기 전에 헤더 행·열·시간 단위를 사용자에게 확인받는다.

    원본 Notion의 find_header_row + align_label(콘솔 열 번호 입력)을
    QDialog로 재구성했다. 확인 버튼을 누르지 않으면 파일은 반영하지 않는다.
    읽는 데이터는 처음 32행 미리보기뿐이며 전체 WAV는 복사하지 않는다.
    """

    def __init__(self, path, kind="labels", sampling_rate=None,
                 hop_length=None, parent=None):
        super().__init__(parent)
        self.file_path = Path(path)
        self.kind = kind
        self.sampling_rate = sampling_rate
        self.hop_length = hop_length
        self.preview = inspect_delimited_file(path)
        self.raw_rows = list(self.preview["preview_rows"])
        self.result_spec = None
        self.setWindowTitle(f"Check CSV header — {self.file_path.name}")
        self.resize(760, 530)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(12, 12, 12, 12)
        shell.setSpacing(8)
        notice = QLabel(
            "Review the highlighted header row and select the correct columns. "
            "The header position, delimiter and units will be saved with this file."
        )
        notice.setWordWrap(True)
        shell.addWidget(notice)

        top = QHBoxLayout()
        top.addWidget(QLabel("Delimiter"))
        self.delimiter_box = QComboBox(self)
        for description, delimiter in (
            ("Comma (,)", ","), ("Tab (\\t)", "\t"),
            ("Semicolon (;)", ";"), ("Pipe (|)", "|")
        ):
            self.delimiter_box.addItem(description, delimiter)
        sep_idx = self.delimiter_box.findData(self.preview["delimiter"])
        if sep_idx >= 0:
            self.delimiter_box.setCurrentIndex(sep_idx)
        top.addWidget(self.delimiter_box)
        top.addWidget(QLabel("Header row (0-based)"))
        self.header_spin = QSpinBox(self)
        self.header_spin.setRange(0, max(0, len(self.raw_rows)-1))
        self.header_spin.setValue(self.preview["header_row"])
        top.addWidget(self.header_spin)
        top.addStretch(1)
        shell.addLayout(top)

        self.table = QTableWidget(self)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        shell.addWidget(self.table, 1)

        self.role_box = QGroupBox("Column mapping", self)
        self.role_layout = QFormLayout(self.role_box)
        self.role_layout.setContentsMargins(8, 12, 8, 8)
        self.role_layout.setSpacing(6)
        self.column_boxes = {}
        role_descriptions = {
            "labels": (("onset_col", "USV onset *"),
                       ("offset_col", "USV offset *"),
                       ("fmin_col", "Min frequency (optional)"),
                       ("fmax_col", "Max frequency (optional)")),
            "waveform": (("amplitude_col", "Signal amplitude/voltage *"),),
        }
        for key, label in role_descriptions.get(kind, ()):
            selector = QComboBox(self.role_box)
            self.role_layout.addRow(label, selector)
            self.column_boxes[key] = selector
        if kind == "labels":
            self.time_unit_box = QComboBox(self.role_box)
            for name, unit in (
                ("Seconds (s)", "s"), ("Milliseconds (ms)", "ms"),
                ("WAV sample indices", "samples"),
                ("STFT frame indices (source hop_length)", "stft_frames")
            ):
                self.time_unit_box.addItem(name, unit)
            self.role_layout.addRow("Onset/offset units *", self.time_unit_box)
            # IMPORTANT: Label-export hop is NOT necessarily the current
            # display STFT hop_length. Preserve the source setting separately.
            self.label_hop_spin = QSpinBox(self.role_box)
            self.label_hop_spin.setRange(1, 1_000_000)
            self.label_hop_spin.setValue(int(hop_length or 512))
            self.label_hop_spin.setToolTip(
                "Frame-index labels: hop_length used when the CSV was exported."
            )
            self.role_layout.addRow("CSV source hop_length (samples)",
                                    self.label_hop_spin)
            self.frequency_unit_box = QComboBox(self.role_box)
            self.frequency_unit_box.addItem("Hz", "Hz")
            self.frequency_unit_box.addItem("kHz", "kHz")
            self.role_layout.addRow("Frequency units", self.frequency_unit_box)
        shell.addWidget(self.role_box)
        self.role_box.setVisible(bool(self.column_boxes))

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel, self
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        shell.addWidget(self.button_box)
        self.delimiter_box.currentIndexChanged.connect(self._update_preview)
        self.header_spin.valueChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self, _=None):
        """원시 행 번호를 유지하면서 선택된 header를 강조 표시."""
        delimiter = self.delimiter_box.currentData()
        self.raw_rows = list(csv.reader(
            self.preview["preview_lines"], delimiter=delimiter
        ))
        header = self.header_spin.value()
        self.table.setRowCount(len(self.raw_rows))
        columns = max((len(row) for row in self.raw_rows), default=0)
        self.table.setColumnCount(columns)
        self.table.setVerticalHeaderLabels(
            [str(index) for index in range(len(self.raw_rows))]
        )
        for ri, row in enumerate(self.raw_rows):
            for ci, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                if ri == header:
                    item.setBackground(QColor("#30525C"))
                self.table.setItem(ri, ci, item)
        column_names = (
            self.raw_rows[header] if header < len(self.raw_rows) else []
        )
        aliases = {
            "onset_col": ("onset", "onset_s", "start", "time_begin",
                          "begin_time", "start_time", "onset_ms"),
            "offset_col": ("offset", "offset_s", "end", "time_end",
                           "end_time", "stop", "offset_ms"),
            "fmin_col": ("fmin_hz", "fmin_khz", "freq_min_hz",
                         "low_freq_hz", "min_frequency"),
            "fmax_col": ("fmax_hz", "fmax_khz", "freq_max_hz",
                         "high_freq_hz", "max_frequency"),
            "amplitude_col": ("amplitude", "voltage", "samples", "signal", "value")
        }
        for key, combo in self.column_boxes.items():
            old = combo.currentData()
            combo.clear()
            optional = key in ("fmin_col", "fmax_col")
            if optional:
                combo.addItem("(none)", None)
            for index, name in enumerate(column_names):
                combo.addItem(f"{index}: {name}", index)
            # 기존에 선택한 열이 있다면 유지. 처음에는 헤더 명칭으로 추천.
            if isinstance(old, int) and old < len(column_names):
                combo.setCurrentIndex(combo.findData(old))
            else:
                normalized = [
                    str(name).strip().lower().replace(" ", "_")
                    for name in column_names
                ]
                found = next(
                    (i for i, value in enumerate(normalized)
                     if value in aliases.get(key, ())), None
                )
                if found is None and not optional and column_names:
                    found = 1 if key == "offset_col" and len(column_names)>1 else 0
                idx = combo.findData(found)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            if key in ("onset_col", "offset_col") and column_names:
                chosen = combo.currentData()
                header_name = str(column_names[chosen]).lower() if chosen is not None else ""
                if "_ms" in header_name:
                    self.time_unit_box.setCurrentIndex(
                        self.time_unit_box.findData("ms")
                    )
        if self.kind == "labels" and column_names:
            selected_freq_col = self.column_boxes["fmin_col"].currentData()
            if selected_freq_col is not None and "khz" in str(
                column_names[selected_freq_col]
            ).lower():
                self.frequency_unit_box.setCurrentIndex(
                    self.frequency_unit_box.findData("kHz")
                )
        self.table.resizeColumnsToContents()

    def accept(self):
        """필수 CSV 열과 시간 단위를 검증한 뒤에만 확인 허용."""
        header = self.header_spin.value()
        names = self.raw_rows[header] if header < len(self.raw_rows) else []
        if not names or len(set(names)) != len(names):
            QMessageBox.warning(
                self, "CSV header", "Header row must contain distinct column names."
            )
            return
        spec = {
            "encoding": self.preview["encoding"],
            "delimiter": self.delimiter_box.currentData(),
            "header_row": header,
        }
        for name, selector in self.column_boxes.items():
            spec[name] = selector.currentData()

        if self.kind == "labels":
            if (spec["onset_col"] is None or spec["offset_col"] is None
                or spec["onset_col"] == spec["offset_col"]):
                QMessageBox.warning(
                    self, "CSV columns", "Choose different onset and offset columns."
                )
                return
            if (spec["fmin_col"] is None) != (spec["fmax_col"] is None):
                QMessageBox.warning(
                    self, "CSV columns", "Set both frequency columns or neither."
                )
                return
            spec["time_unit"] = self.time_unit_box.currentData()
            spec["frequency_unit"] = self.frequency_unit_box.currentData()
            spec["label_hop_length"] = self.label_hop_spin.value()
            if spec["time_unit"] in ("samples", "stft_frames") and (
                not self.sampling_rate or self.sampling_rate <= 0
            ):
                QMessageBox.warning(
                    self, "Sampling rate", "Load the corresponding WAV before "
                    "using sample/frame-index label units."
                )
                return
            if (spec["time_unit"] == "stft_frames"
                and self.label_hop_spin.value() <= 0):
                QMessageBox.warning(
                    self, "STFT frames", "Valid CSV export hop_length required."
                )
                return
        elif self.kind == "waveform":
            if spec.get("amplitude_col") is None:
                QMessageBox.warning(self, "CSV columns", "Select a waveform column.")
                return
        else:
            normalized = {
                str(value).strip().lower().replace(".", "_")
                for value in names
            }
            required_by_kind = {
                "tracking": {"frame_idx"},
                "training": {"r_corr"},
                "validation": {"onset_s", "offset_s", "mouse_id"},
            }
            missing = required_by_kind.get(self.kind, set()) - normalized
            if missing:
                QMessageBox.warning(
                    self, "CSV header",
                    f"Required columns not found: {sorted(missing)}. "
                    "Select the correct header row or edit the CSV."
                )
                return
        try:
            df = read_delimited_table(self.file_path, spec)
            if df.empty and self.kind != "labels":
                raise ValueError("No data rows under the selected header.")
            if self.kind == "labels":
                for key in ("onset_col", "offset_col"):
                    pd.to_numeric(df.iloc[:, spec[key]], errors="raise")
            if self.kind == "waveform":
                pd.to_numeric(df.iloc[:, spec["amplitude_col"]], errors="raise")
        except Exception as err:
            QMessageBox.warning(self, "CSV import", str(err))
            return
        self.result_spec = spec
        super().accept()


class MainWindow(QMainWindow):
    """UI와 분석 파이프라인을 조립하는 controller. 데이터 모델과 알고리즘은 외부 모듈."""

    VIEW_SECONDS = 3.0

    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.spectrogram_lut = spectrogram_lookup_table()
        # 고정 컬러스케일: 모든 WAV 채널·모든 시간 구간에 하나의 범위를 사용.
        # 단위는 상대 디지털 STFT 로그 레벨이며 물리적 dB SPL이 아니다.
        self.spectrogram_levels_db = (-100.0, 0.0)
        self.audio_slots = []  # 길이 = 마이크 개수, 각 원소 AudioData 또는 None
        self.events = []       # 검출/수입된 공통 USVEvent 리스트
        self.spectrogram_views = []
        self.channel_fields = []
        self.results = []
        self.analysis_generation = 0  # 설정이 바뀐 뒤 도착하는 이전 분석 결과를 무시.
        self.tracking_df = None
        self.anchor_mapping = None
        self.geometry = None
        self.empirical_cfg = None
        self.empirical_threshold = None
        self.analysis_results = {"ratio": [], "distance": [], "geometry": []}
        self.validation_metrics = {}
        self.training_path = None
        self.overlay_items = []
        # Each microphone has its OWN CSV/TXT labels + reviewed header mapping.
        self.label_path = None  # Legacy alias for first label (old sessions)
        self.label_paths = []
        self.label_specs = []
        self.label_events = []
        self.label_rows = []
        self.audio_csv_specs = []
        self.tracking_csv_spec = None
        self.tracking_path = None
        # 두 매체의 동일 사건에 대응하는 기준점. None이면 아직 지정되지 않음.
        self.audio_start_s = None
        self.video_start_s = None
        self.sync = SyncConfig()
        self.has_video = False
        self.video_duration_ms = 0
        self.window_start_ms = 0
        # Three playback modes are independent:
        # audio-only -> real-time 1x preview of selected microphone
        # video-only -> video without moving the audio spectrogram
        # play-all   -> video clock + synchronized spectrogram cursor
        self._play_all_active = False
        self._audio_preview_active = False
        self._audio_preview_cursor_s = 0.0
        self._audio_preview_start_s = 0.0
        self._audio_preview_end_s = 0.0
        self._audio_preview_time_scale = 1.0
        self._audio_preview_channel_idx = 0
        self._audio_preview_file_counter = 0
        self._audio_preview_current_path = None
        self._audio_preview_tmpdir = None
        self._audio_cursor_ms = 0  # slider and playhead use ORIGINAL sample time.

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.render_id = 0
        self.render_busy = False
        self.analysis_busy = False
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(90)  # 드래그 중 불필요한 STFT 재연산 방지
        self.render_timer.timeout.connect(self._start_render)

        self._setup_channel_controls()
        self._setup_spectrogram_area()
        self._setup_video_player()
        self._setup_timeline_markers()
        self._setup_playback_controls()
        self._setup_analysis_controls()
        # Default Analyzing의 기존 위젯을 보존하면서 6개 GroupBox로 정리.
        self._group_settings_controls()
        # Input Files GroupBox: create one USV label file row per audio channel.
        self._setup_label_controls()
        # Qt Designer 원본은 그대로 두고 Splitter·패널·스크롤만 런타임 보정.
        self._setup_responsive_layout()
        self._connect_signals()
        # Qt positionChanged is often throttled; poll backend position at 50ms.
        # This timer only READS media positions, never fabricates playback time.
        self.playback_refresh_timer = QTimer(self)
        self.playback_refresh_timer.setInterval(50)
        self.playback_refresh_timer.timeout.connect(self._poll_playback_positions)
        self.playback_refresh_timer.start()

        # GitHub 최신 UI에 이미 고정된 두 개의 Audio 행을 재사용한다.
        self._rebuild_plots()
        self._refresh_audio_state()

    def _wrap_analysis_tab_in_scroll(self, page, source_layout, name):
        """Designer 탭에 놓인 기존 위젯들을 보존하면서 탭 전체를 세로 스크롤.

        source_layout은 이미 page에 설정된 원래 QVBoxLayout이다.
        각 QLayoutItem을 안전하게 새 콘텐츠 위젯으로 옮긴 다음, 원래 레이아웃에는
        스크롤 영역 하나만 넣는다. 따라서 기존 objectName/Signal 연결은 유지된다.
        """
        contents = QWidget(page)
        contents.setObjectName(f"scroll_content_{name}")
        inner = QVBoxLayout(contents)
        inner.setContentsMargins(7, 7, 7, 7)
        inner.setSpacing(7)

        while source_layout.count():
            item = source_layout.takeAt(0)
            widget = item.widget()
            nested = item.layout()
            if widget is not None:
                # 기존 위젯 객체를 재사용한다. 새 위젯으로 중복 생성하지 않는다.
                widget.setParent(contents)
                inner.addWidget(widget)
            elif nested is not None:
                inner.addLayout(nested)
            else:
                inner.addItem(item)

        # 긴 위젯들이 화면 아래로 넘치면 탭 내부에서 스크롤한다.
        inner.addStretch(1)
        area = QScrollArea(page)
        area.setObjectName(f"analysis_scroll_{name}")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setWidget(contents)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(area)
        return area

    def _setup_responsive_layout(self):
        """창 크기 변화에 대응하는 2단 Splitter와 compact analysis 패널.

        UI 구조는 다음과 같다:
          splitter_2 (Vertical): tab_file_panel / work_space
          splitter   (Horizontal): content_container / settings_panel

        기존 Designer에는 오른쪽 분석 페이지 전체 스크롤이 없어서
        항목이 많아질수록 결과가 잘리거나 splitter가 눌리지 않는 문제가 있었다.
        """
        # 손잡이의 실제 클릭 영역을 넓히고, 한쪽 패널이 0으로 접히지 않게 한다.
        for splitter in (self.ui.splitter_2, self.ui.splitter):
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(9)
            splitter.setOpaqueResize(True)

        # 기본 최소 너비 600px이 오른쪽 패널의 조절 폭을 막던 문제를 완화한다.
        self.ui.content_container.setMinimumWidth(400)
        self.ui.settings_panel.setMinimumWidth(345)
        self.ui.tab_analyze.setUsesScrollButtons(True)

        # 오디오 채널 기본 2행과 + 버튼이 세로 splitter에 가려지지 않도록 한다.
        self.ui.tab_file_panel.setMinimumHeight(165)
        self.ui.tab_file_panel.setMaximumHeight(245)
        self.ui.tab_file_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.ui.scroll_audio_file.setMinimumHeight(95)
        self.ui.scroll_audio_file.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.ui.horizontalLayout_13.setContentsMargins(3, 3, 3, 3)
        self.ui.verticalLayout_14.setContentsMargins(5, 4, 5, 4)
        self.ui.verticalLayout_14.setSpacing(4)
        self.ui.horizontalLayout_15.setSpacing(6)
        self.ui.horizontalLayout_14.setSpacing(6)

        # 불필요한 가장자리 여백을 줄이고 분할 화면의 활용 면적을 넓힌다.
        self.ui.verticalLayout_3.setContentsMargins(4, 4, 4, 4)
        self.ui.verticalLayout.setContentsMargins(4, 4, 4, 4)
        self.ui.verticalLayout_5.setSpacing(7)
        self.ui.verticalLayout_8.setSpacing(6)
        self.ui.verticalLayout_10.setSpacing(6)
        self.ui.verticalLayout_15.setContentsMargins(5, 5, 5, 5)
        self.ui.verticalLayout_15.setSpacing(4)
        # 재생 패널 크기와 내부 패딩은 _setup_playback_controls()가 관리한다.
        # 여기서는 세로 스크롤 영역이 패널을 아래로 밀지 않도록 크기 정책만 조정.
        self.ui.scrollarea_spectrogram.setMinimumHeight(0)
        self.ui.scrollarea_spectrogram.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        self.ui.verticalLayout_9.setContentsMargins(2, 2, 2, 2)
        self.ui.verticalLayout_7.setContentsMargins(2, 2, 2, 2)
        self.ui.verticalLayout_9.setSpacing(3)
        self.ui.verticalLayout_7.setSpacing(3)

        # QTextEdit가 들어 있는 기존 중첩 스크롤 박스를 무한히 늘리지 않음.
        # QTextEdit 자체의 내부 스크롤이 결과의 긴 문자열을 담당한다.
        # 결과창 전체를 크게 고정하지 않고 각 창 내부의 QTextEdit가 스크롤.
        for view, editor in (
            (self.ui.scrollarea_result, self.ui.text_result),
            (self.ui.scrollarea_dist_result, self.ui.text_dist_result),
            (self.ui.scrollarea_geo_result, self.ui.text_geo_result),
        ):
            view.setMinimumHeight(112)
            view.setMaximumHeight(168)
            view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            editor.setMinimumHeight(86)

        self.ui.group_distance_corr.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.ui.group_geo_corr.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        # 오디오 그래프 높이를 확보하고 우측 설정을 독립적으로 스크롤한다.
        self.analysis_default_scroll = self._wrap_analysis_tab_in_scroll(
            self.ui.tab_default_analyze, self.ui.verticalLayout_8, "default"
        )
        self.analysis_test_scroll = self._wrap_analysis_tab_in_scroll(
            self.ui.tab_test_analyze, self.ui.verticalLayout_10, "test"
        )

        # 생성된 splitter의 실제 크기는 show() 이후 확정되므로 다음 이벤트에서 적용.
        QTimer.singleShot(0, self._set_initial_splitter_sizes)

    def _set_initial_splitter_sizes(self):
        """초기 비율만 설정: 이후에는 사용자가 드래그한 위치를 다시 덮어쓰지 않는다."""
        available_width = max(self.ui.splitter.width(), 760)
        sidebar_width = max(345, min(465, round(available_width * 0.32)))
        self.ui.splitter.setSizes([
            max(400, available_width - sidebar_width), sidebar_width
        ])

        total_height = max(self.ui.splitter_2.height(), 520)
        self.ui.splitter_2.setSizes([185, max(330, total_height - 185)])

    def _setup_label_controls(self):
        """1 Audio channel ↔ 1 USV label input, preserving the Designer Ch1 row.

        Existing label widgets are reused. The other N-1 label rows are added
        to the same Input Files grid, BEFORE the Video Tracking row.
        The mapping follows channel identity when extra channels are removed.
        """
        count = len(self.audio_slots)
        self.label_paths = [None] * count
        self.label_specs = [None] * count
        self.label_events = [[] for _ in range(count)]
        self.audio_csv_specs = [None] * count
        self.ui.line_label_file.setReadOnly(True)
        self.ui.line_label_file.setPlaceholderText("Select label CSV / TXT")
        self.label_rows = [{
            "label": self.ui.lbl_label_file,
            "path": self.ui.line_label_file,
            "browse": self.ui.btn_label_browse,
            "dynamic": False,
        }]
        for _ in range(1, count):
            self._append_label_row()
        self._reflow_label_grid()

    def _append_label_row(self):
        """Add an independent label browser for the newly added microphone."""
        index = len(self.label_rows)
        parent = self.ui.tab_default_analyze
        label = QLabel(f"Ch {index+1} Label File", parent)
        path = QLineEdit(parent)
        path.setObjectName(f"line_label_path_{index+1}")
        path.setReadOnly(True)
        path.setPlaceholderText("Select label CSV / TXT")
        browse = QPushButton("Browse", parent)
        browse.setObjectName(f"btn_label_browse_{index+1}")
        row = {"label": label, "path": path, "browse": browse, "dynamic": True}
        self.label_rows.append(row)
        browse.clicked.connect(
            lambda checked=False, record=row: self.open_label(
                self.label_rows.index(record)
            )
        )
        return row

    def _reflow_label_grid(self):
        """Reorder the existing widgets; tracking input always follows N labels."""
        if not hasattr(self, "label_rows"):
            return
        grid = self.ui.gridLayout_2
        tracking = (
            self.ui.lbl_tracking_file,
            self.ui.line_tracking_file,
            self.ui.lbl_tracking_browse,
        )
        for row in self.label_rows:
            for key in ("label", "path", "browse"):
                grid.removeWidget(row[key])
        for widget in tracking:
            grid.removeWidget(widget)

        for index, row in enumerate(self.label_rows):
            row["label"].setText(f"Ch {index+1} Label File")
            row["label"].setMinimumWidth(100)
            row["path"].setToolTip(
                self.label_paths[index] if index < len(self.label_paths)
                and self.label_paths[index] else f"Labels for Ch {index+1}"
            )
            if index < len(self.label_paths):
                row["path"].setText(self.label_paths[index] or "")
            grid.addWidget(row["label"], index, 0)
            grid.addWidget(row["path"], index, 1)
            grid.addWidget(row["browse"], index, 2)

        track_row = len(self.label_rows)
        for column, widget in enumerate(tracking):
            grid.addWidget(widget, track_row, column)
        grid.setColumnStretch(1, 1)

    def _recompute_label_events(self):
        """Merge per-channel labels ONCE; do not duplicate the same USV N times."""
        self.label_path = self.label_paths[0] if self.label_paths else None
        if (len(self.label_events) != len(self.audio_slots)
            or not all(self.label_paths)):
            self.events = []
        else:
            self.events = merge_channel_events(
                self.label_events, tolerance_s=0.005, min_iou=0.60
            )
        self._invalidate_results()
        ready = sum(bool(path) for path in self.label_paths)
        self.statusBar().showMessage(
            f"USV labels: {ready}/{len(self.audio_slots)} channels, "
            f"{len(self.events)} merged event(s)", 7000
        )

    def _reload_label_events_for_audio(self):
        """WAV sample rate updates must propagate into sample/frame label units."""
        if not hasattr(self, "label_paths"):
            return
        updated = [[] for _ in self.label_paths]
        for i, (path, spec) in enumerate(zip(self.label_paths, self.label_specs)):
            if not path:
                continue
            audio = self.audio_slots[i] if i < len(self.audio_slots) else None
            updated[i] = load_labels(
                path, table_spec=spec,
                sr=audio.sr if audio is not None else None,
                hop_length=self.ui.spin_hop_length.value()
            )
            if audio is not None and any(
                event.offset_s > audio.duration_s for event in updated[i]
            ):
                raise ValueError(
                    f"Ch {i+1} label timestamps exceed its WAV length. "
                    "Review time units and selected columns."
                )
        self.label_events = updated
        self._recompute_label_events()

    def _setup_channel_controls(self):
        """GitHub 최신 UI의 처음 두 오디오 입력 행을 그대로 사용한다.

        Designer의 scroll_audio_file / verticalLayout_14가 추가 채널의 부모.
        1~2행은 삭제할 수 없고 세 번째 행부터만 × 버튼이 나타난다.
        QGridLayout(file_panel)은 최신 GitHub UI에 존재하지 않으므로 사용 금지.
        """
        self.audio_rows = []
        self.audio_slots = [None, None]
        self.channel_host = self.ui.scrollAreaWidgetContents_5
        self.channel_layout = self.ui.verticalLayout_14
        self.channel_scroll = self.ui.scroll_audio_file
        self.channel_scroll.setWidgetResizable(True)

        # 원래 Designer에 존재하는 두 줄 그대로 적용 (새 위젯으로 중복 생성 금지).
        fixed_rows = (
            (self.ui.lbl_subject_id, self.ui.lbl_audio_path,
             self.ui.line_subject_id, self.ui.line_audio_path,
             self.ui.btn_audio_browse),
            (self.ui.lbl_subject_id_2, self.ui.lbl_audio_path_2,
             self.ui.line_subject_id_2, self.ui.line_audio_path_2,
             self.ui.btn_audio_browse_2),
        )
        for index, (subject_label, label, subject, path, browse) in enumerate(fixed_rows):
            # 기존 Designer의 두 행도 추가 행과 동일한 라벨 정책을 사용한다.
            subject_label.setText("Subject ID")
            label.setText(f"Ch {index+1} File Path")
            subject.setText(subject.text().strip() or f"mouse_{index+1}")
            path.setReadOnly(True)
            self.audio_rows.append({
                "panel": None, "subject_label": subject_label,
                "label": label, "subject": subject,
                "path": path, "browse": browse, "delete": None
            })
            subject.editingFinished.connect(self._on_audio_subject_changed)

        # 추가 버튼은 둘째 줄 아래. + 눌러 추가한 줄만 × 삭제 가능.
        self.add_mic_button = QPushButton("+ Add microphone", self.channel_host)
        self.channel_layout.addWidget(self.add_mic_button)
        self.add_mic_button.clicked.connect(self.add_audio_channel)
        self._sync_channel_fields()

    def _setup_spectrogram_area(self):
        """Re-use latest Designer's audio scroll area and graph container.

        Note: soectrogram_container is the original objectName spelling.
        verticalLayout_11 now belongs to the distance-correction group.
        """
        self.graph_scroll = self.ui.scrollarea_spectrogram
        # QScrollArea viewport도 별도 QWidget이므로 명확한 이름으로 QSS를 적용.
        # 그렇지 않으면 데이터가 비어 있을 때 배경이 주변 패널과 다르게 보임.
        self.graph_scroll.viewport().setObjectName("spectrogram_scroll_viewport")
        self.graph_scroll.setWidgetResizable(True)
        self.graph_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.graph_host = self.ui.spectrogram_container
        self.graph_layout = self.graph_host.layout()
        if self.graph_layout is None:
            self.graph_layout = QVBoxLayout(self.graph_host)
        # Avoid stacking margins from scroll widget + graph host + each channel.
        # A single dark surface is used for empty space, axis area and STFT canvas.
        self.graph_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.graph_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.ui.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.ui.horizontalLayout_2.setSpacing(0)
        self.ui.verticalLayout_9.setContentsMargins(3, 3, 3, 3)
        self.ui.verticalLayout_9.setSpacing(4)
        self.ui.verticalLayout_9.setStretch(0, 1)  # graph area gets remaining height
        self.ui.verticalLayout_9.setStretch(1, 0)  # keep timeline compact
        self.graph_layout.setContentsMargins(2, 2, 2, 2)
        self.graph_layout.setSpacing(3)

    def _setup_video_player(self):
        """tab_video의 video QWidget에 별도 비디오 렌더러 삽입."""
        self.video_widget = QVideoWidget(self.ui.video_container)
        layout = self.ui.video_container.layout()
        if layout is None:
            layout = QVBoxLayout(self.ui.video_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_widget)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setPlaybackRate(1.0)  # video wall-clock: 1s -> 1s
        self.ui.btn_play_all.setEnabled(False)

        # Separate audio decoder/output. Ultrasound WAV cannot generally be
        # rendered directly on PC speakers at the acquisition sample rate.
        # A small temporary 48-kHz PCM preview is prepared on demand below.
        self.audio_preview_player = QMediaPlayer(self)
        self.audio_preview_output = QAudioOutput(self)
        self.audio_preview_output.setVolume(0.6)
        self.audio_preview_player.setAudioOutput(self.audio_preview_output)
        self._audio_preview_tmpdir = tempfile.TemporaryDirectory(
            prefix="usvoc_audio_preview_"
        )

    def _setup_timeline_markers(self):
        """Audio·Video·하단 공통 타임라인 위에 실제 시간 기준점 표시 위젯 생성."""
        # Audio 초록색, Video/공통 컨트롤 주황색으로 매체를 구분한다.
        self.audio_slider_marker = TimelineStartMarker(
            self.ui.slider_timeline_audio, color="#40C9DF"
        )
        self.video_slider_marker = TimelineStartMarker(
            self.ui.slider_timeline_video, color="#FFB85C"
        )
        # 하단 전체 재생 슬라이더는 비디오 position(ms)을 사용한다.
        self.shared_slider_marker = TimelineStartMarker(
            self.ui.slider_timeline_all, color="#FFB85C"
        )

    def _playback_frame_from_groupbox(self, groupbox, source_layout):
        """Qt Designer QGroupBox 재생 컨테이너를 제목 공간이 없는 QFrame으로 교체.

        GroupBox는 빈 title 상태에서도 Qt style에 따른 상단 여백을 예약할 수
        있다. QFrame은 콘텐츠 상단에 제목 영역이 없어 버튼/타임라인의
        수직 중앙 정렬이 정확하다. 기존 위젯과 signal 연결은 재사용한다.
        """
        parent = groupbox.parentWidget()
        parent_layout = parent.layout()
        if parent_layout is None:
            raise RuntimeError("Playback panel parent has no layout.")
        index = parent_layout.indexOf(groupbox)
        if index < 0:
            raise RuntimeError(
                f"Playback panel {groupbox.objectName()} is not in its parent layout."
            )

        panel_name = groupbox.objectName()
        frame = QFrame(parent)
        frame.setObjectName(panel_name)  # 기존 QSS/objectName 참조를 보존.
        frame.setFrameShape(QFrame.Shape.NoFrame)
        frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        frame.setFixedHeight(40)

        # 개별 스타일도 적용: 로컬 style.qss가 과거 버전이어도 패널은 동일.
        # QFrame 자체에만 적용하므로 하위 QPushButton/QSlider는 영향 없음.
        frame.setStyleSheet(
            f"QFrame#{panel_name} {{"
            "background-color: #242424;"
            "border: 1px solid #42474B;"
            "border-radius: 6px;"
            "padding: 0px;"
            "margin: 0px;"
            "}"
        )

        layout = QHBoxLayout(frame)
        layout.setSpacing(6)
        layout.setContentsMargins(6, 4, 6, 4)
        # Sync marker QLabel까지 원래 순서 그대로 새 프레임으로 옮긴다.
        while source_layout.count():
            item = source_layout.takeAt(0)
            widget = item.widget()
            nested_layout = item.layout()
            if widget is not None:
                layout.addWidget(widget)  # widget parent → frame
            elif nested_layout is not None:
                layout.addLayout(nested_layout)
            else:
                layout.addItem(item)

        parent_layout.insertWidget(index, frame)
        parent_layout.removeWidget(groupbox)
        groupbox.hide()
        groupbox.setParent(None)
        groupbox.deleteLater()
        return frame, layout

    def _setup_playback_controls(self):
        """Audio/Video 독립 재생 버튼과 공통 재생 패널의 간격·정렬을 통일.

        Qt Designer의 Ui_MainWindow를 수정하지 않고 원래 QHBoxLayout에 삽입:
        Audio: Sync | Play/Pause | channel | timeline | time | marker
        Video: Sync | Play/Pause | timeline | time | marker
        All:   Play/Pause | common timeline | time

        Qt GroupBox의 실제 테두리·레이아웃 높이를 고려해 52px를 확보하고
        그래프 쪽 스크롤 영역만 줄어들게 설정해 컨트롤이 잘리지 않도록 한다.
        """
        # QGroupBox가 생성하는 상단 title 영역을 없애기 위해 세 패널의
        # 기존 layout/widgets를 QFrame으로 옮긴다. Qt Designer 원본은 보존.
        for panel_attr, layout_attr in (
            ("playback_panel_audio", "horizontalLayout_4"),
            ("playback_panel_4", "horizontalLayout_5"),
            ("playback_panel_all", "horizontalLayout_3"),
        ):
            frame, row = self._playback_frame_from_groupbox(
                getattr(self.ui, panel_attr), getattr(self.ui, layout_attr)
            )
            setattr(self.ui, panel_attr, frame)
            setattr(self.ui, layout_attr, row)

        self.btn_audio_play = QPushButton("▶", self.ui.playback_panel_audio)
        self.btn_audio_play.setObjectName("btn_audio_play")
        self.btn_audio_play.setFixedSize(38, 28)
        self.btn_audio_play.setToolTip(
            "선택한 마이크 1x 실시간 재생. 초음파는 속도 대신 주파수를 낮춰 듣기."
        )
        self.ui.horizontalLayout_4.insertWidget(1, self.btn_audio_play)

        self.audio_channel_select = QComboBox(self.ui.playback_panel_audio)
        self.audio_channel_select.setObjectName("audio_playback_channel")
        self.audio_channel_select.setFixedWidth(68)
        self.audio_channel_select.setFixedHeight(28)
        self.audio_channel_select.setToolTip("소리를 미리 들을 마이크 채널")
        self.ui.horizontalLayout_4.insertWidget(2, self.audio_channel_select)
        self._update_audio_playback_channels()

        self.btn_video_play = QPushButton("▶", self.ui.playback_panel_4)
        self.btn_video_play.setObjectName("btn_video_play")
        self.btn_video_play.setFixedSize(38, 28)
        self.btn_video_play.setToolTip("영상만 재생·일시정지 (오디오 화면은 이동하지 않음)")
        self.ui.horizontalLayout_5.insertWidget(1, self.btn_video_play)
        self.btn_video_play.setEnabled(False)

        self.ui.btn_play_all.setObjectName("btn_play_all")
        self.ui.btn_play_all.setText("▶")
        self.ui.btn_play_all.setToolTip(
            "동기화된 영상과 Audio 스펙트로그램을 공통 타임라인으로 재생"
        )
        self.ui.btn_play_all.setFixedSize(38, 28)

        playback_rows = (
            (self.ui.playback_panel_audio, self.ui.horizontalLayout_4,
             self.ui.btn_starting_audio, self.ui.slider_timeline_audio,
             self.ui.lbl_time_audio),
            (self.ui.playback_panel_4, self.ui.horizontalLayout_5,
             self.ui.btn_starting_video, self.ui.slider_timeline_video,
             self.ui.lbl_time_video),
            (self.ui.playback_panel_all, self.ui.horizontalLayout_3,
             self.ui.btn_play_all, self.ui.slider_timeline_all,
             self.ui.label_time_all),
        )
        for panel, layout, first_button, slider, time_label in playback_rows:
            panel.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            # Title-less QFrame: 버튼 28px + 위아래 여백 4px씩.
            # Qt의 GroupBox title 영역이 없으므로 아래로 쏠리지 않는다.
            panel.setFixedHeight(40)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(6)

            slider.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            slider.setFixedHeight(26)
            time_label.setMinimumWidth(82)
            time_label.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            time_label.setAlignment(Qt.AlignmentFlag.AlignVCenter |
                                    Qt.AlignmentFlag.AlignRight)
            for widget in (first_button, slider, time_label):
                layout.setAlignment(widget, Qt.AlignmentFlag.AlignVCenter)
            layout.setStretchFactor(slider, 1)

        for sync_btn in (self.ui.btn_starting_audio, self.ui.btn_starting_video):
            sync_btn.setFixedSize(70, 28)
        self.ui.horizontalLayout_4.setAlignment(
            self.btn_audio_play, Qt.AlignmentFlag.AlignVCenter
        )
        self.ui.horizontalLayout_4.setAlignment(
            self.audio_channel_select, Qt.AlignmentFlag.AlignVCenter
        )
        self.ui.horizontalLayout_5.setAlignment(
            self.btn_video_play, Qt.AlignmentFlag.AlignVCenter
        )

        # These widgets live outside the splitter; they must not stretch vertically.
        self.ui.verticalLayout_9.setStretch(0, 1)
        self.ui.verticalLayout_9.setStretch(1, 0)
        self.ui.verticalLayout_7.setStretch(0, 1)
        self.ui.verticalLayout_7.setStretch(1, 0)
        self.ui.verticalLayout_15.setSpacing(5)

    def _update_audio_playback_channels(self):
        """마이크 행을 추가/삭제하면 선택 메뉴도 현재 채널 목록과 일치시킨다."""
        if not hasattr(self, "audio_channel_select"):
            return
        old = self.audio_channel_select.currentData()
        with QSignalBlocker(self.audio_channel_select):
            self.audio_channel_select.clear()
            for i in range(len(self.audio_slots)):
                self.audio_channel_select.addItem(f"Ch {i+1}", i)
            if isinstance(old, int) and old < len(self.audio_slots):
                self.audio_channel_select.setCurrentIndex(old)
        self.btn_audio_play.setEnabled(any(a is not None for a in self.audio_slots))

    def _group_settings_controls(self):
        """Default Analyzing 설정을 기능별 6개 GroupBox로 재배치한다.

        기존 Qt Designer 위젯 자체를 재사용하므로 Browse/FFT/Sync/Geometry/
        Validation의 objectName과 signal 연결을 유지한다.
        원본 UI 탭과 ui_mainwindow.py는 건드리지 않는다.

        verticalLayout_5의 순서 (Qt Designer의 두 layout + 동적 입력 13개):
        0 data inputs, 1 STFT inputs, 2-5 Sync, 6-8 display,
        9-11 geometry/anchor, 12-14 training/validation.
        구조가 달라진 경우 억지로 이동하지 않고 기존 배치를 유지한다.
        """
        root = self.ui.verticalLayout_5
        if (root.count() != 15 or
            root.itemAt(0).layout() is None or
            root.itemAt(1).layout() is None or
            root.itemAt(0).layout().objectName() != "verticalLayout_2" or
            root.itemAt(1).layout().objectName() != "verticalLayout_4"):
            self.statusBar().showMessage(
                "Settings grouping skipped: unexpected Qt Designer layout.", 8000
            )
            return

        # 구분을 위한 QGroupBox 제목이 생기므로 내부의 중복 제목은 숨긴다.
        self.ui.lbl_analysis_file.hide()
        self.ui.lbl_analysis_settings.hide()
        self.ui.lbl_label_file.setText("USV Labels")
        self.ui.lbl_tracking_file.setText("SLEAP Tracks")
        self.ui.gridLayout_2.setColumnStretch(1, 1)
        self.ui.formLayout.setSizeConstraint(
            QLayout.SizeConstraint.SetDefaultConstraint
        )

        # 이동 전 구조를 검증했고, 같은 위젯 인스턴스를 새 그룹에 옮긴다.
        original_items = [root.takeAt(0) for _ in range(15)]
        groups = [
            ("Input Files", "settings_group_files", [0]),
            ("STFT Parameters", "settings_group_stft", [1]),
            ("Time Synchronization", "settings_group_sync", [2, 3, 4, 5]),
            ("Spectrogram Display", "settings_group_display", [6, 7, 8]),
            ("Geometry & Anchor", "settings_group_geometry", [9, 10, 11]),
            ("Model Training & Validation", "settings_group_validation",
             [12, 13, 14]),
        ]
        for heading, obj_name, member_indexes in groups:
            group = QGroupBox(heading, self.ui.tab_default_analyze)
            group.setObjectName(obj_name)
            group.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            box_layout = QVBoxLayout(group)
            box_layout.setContentsMargins(8, 10, 8, 7)
            box_layout.setSpacing(5)

            for index in member_indexes:
                item = original_items[index]
                widget = item.widget()
                nested = item.layout()
                if nested is not None:
                    # QLayoutItem을 꺼내도 QObject 부모가 남는 경우가 있어
                    # 새 GroupBox에 추가하기 전에 부모를 명시적으로 해제한다.
                    nested.setParent(None)
                    box_layout.addLayout(nested)
                elif widget is not None:
                    box_layout.addWidget(widget)
                else:
                    box_layout.addItem(item)
            if obj_name == "settings_group_geometry":
                box_layout.addWidget(self.label_anchor_mapping)
            root.addWidget(group)
        root.setSpacing(7)

    def _setup_analysis_controls(self):
        """기존 settings_panel에 추적/캘리브레이션 및 결과 내보내기 버튼 추가."""
        # Make distance and combined geometry visibly distinct in Test tab.
        self.ui.group_distance_corr.setTitle("Distance-only Correction")
        self.ui.group_distance_corr.setToolTip(
            "Only nose-to-nose distance ω_d. No torso/heading/angle required."
        )
        self.ui.btn_distance_corr.setText("Run Distance-only")
        self.ui.btn_distance_corr.setToolTip(
            "R_corr = ln(A/B) / max(omega_d, omega_min); "
            "requires validated Audio↔Video mapping and training threshold."
        )
        self.ui.group_geo_corr.setTitle("Distance + Angle Correction")
        self.ui.btn_geo_corr.setText("Run Distance + Angle")
        self.ui.btn_geo_corr.setToolTip(
            "Uses distance omega_d and direction omega_theta together."
        )
        self.offset_spin = QDoubleSpinBox(self.ui.settings_panel)
        self.offset_spin.setRange(-3600, 3600)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSuffix(" s")
        # offset은 두 Sync 버튼으로 산출되는 읽기 전용 값이다.
        self.offset_spin.setToolTip("두 Sync 기준점의 차이: Audio start - Video start")
        self.offset_spin.setReadOnly(True)
        self.offset_spin.setEnabled(False)
        self.ui.verticalLayout_5.addWidget(QLabel("Audio / Video offset (s)"))
        self.ui.verticalLayout_5.addWidget(self.offset_spin)
        self.label_sync_state = QLabel("Sync: Audio · Video 시작점을 각각 지정하세요.")
        self.label_sync_state.setWordWrap(True)
        self.ui.verticalLayout_5.addWidget(self.label_sync_state)
        self.reset_sync_button = QPushButton("Reset Sync starts", self.ui.settings_panel)
        self.ui.verticalLayout_5.addWidget(self.reset_sync_button)

        # 스펙트로그램 색상 범위는 모든 WAV·전 구간 공통으로 고정.
        # 사용자가 명시적으로 바꾸지 않는 한 재생 중에도 값은 변경되지 않음.
        self.ui.verticalLayout_5.addWidget(
            QLabel("Spectrogram intensity (dB rel.)")
        )
        self.color_scale_form = QFormLayout()
        self.color_min_spin = QDoubleSpinBox(self.ui.settings_panel)
        self.color_max_spin = QDoubleSpinBox(self.ui.settings_panel)
        for spin in (self.color_min_spin, self.color_max_spin):
            spin.setRange(-180.0, 40.0)
            spin.setDecimals(0)
            spin.setSingleStep(5.0)
            spin.setSuffix(" dB")
        self.color_min_spin.setValue(self.spectrogram_levels_db[0])
        self.color_max_spin.setValue(self.spectrogram_levels_db[1])
        self.color_scale_form.addRow("Dark / Min", self.color_min_spin)
        self.color_scale_form.addRow("Bright / Max", self.color_max_spin)
        self.ui.verticalLayout_5.addLayout(self.color_scale_form)
        self.color_scale_info = QLabel(
            "Fixed for all microphones and time windows."
        )
        self.color_scale_info.setWordWrap(True)
        self.ui.verticalLayout_5.addWidget(self.color_scale_info)
        # 각 탭 타임라인 바로 옆에 선택된 기준점을 고정 표시한다.
        self.audio_start_label = QLabel("A: —", self.ui.playback_panel_audio)
        self.video_start_label = QLabel("V: —", self.ui.playback_panel_4)
        self.ui.horizontalLayout_4.addWidget(self.audio_start_label)
        self.ui.horizontalLayout_5.addWidget(self.video_start_label)
        self.audio_start_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        )
        self.video_start_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        )

        self.geometry_button = QPushButton("Load geometry.json", self.ui.settings_panel)
        self.geometry_button.setToolTip("마이크 실측 좌표와 camera homography를 로드.")
        self.ui.verticalLayout_5.addWidget(self.geometry_button)
        self.label_geometry = QLabel("Geometry: none (ratio-only)", self.ui.settings_panel)
        self.ui.verticalLayout_5.addWidget(self.label_geometry)

        self.anchor_button = QPushButton(
            "Match Audio ↔ Video subjects", self.ui.settings_panel
        )
        self.anchor_button.setToolTip(
            "Select a SLEAP video track for every Audio channel/Subject ID. "
            "Never infer physical identities from track ordering."
        )
        self.ui.verticalLayout_5.addWidget(self.anchor_button)
        # Attach to Geometry & Anchor GroupBox after _group_settings_controls
        # re-homes the initial 15 items; do not alter root count=15.
        self.label_anchor_mapping = QLabel(
            "Audio ↔ Video mapping: not confirmed", self.ui.settings_panel
        )
        self.label_anchor_mapping.setObjectName("label_audio_video_mapping")
        self.label_anchor_mapping.setWordWrap(True)

        # 별도의 정답 데이터로 baseline/보정식의 효과를 평가한다.
        # ground-truth CSV는 학습/임계값 선택에 사용한 파일과 달라야 한다.
        # Training R_corr 데이터의 median/MAD를 학습한다 (validation 분리 필수).
        self.training_button = QPushButton("Fit median/MAD from training CSV", self.ui.settings_panel)
        self.training_button.setToolTip(
            "같은 empirical 설정에서 계산된 R_corr 열을 사용해 R_mid와 MAD 학습"
        )
        self.ui.verticalLayout_5.addWidget(self.training_button)
        self.validation_button = QPushButton("Validate with ground-truth CSV", self.ui.settings_panel)
        self.validation_button.setToolTip(
            "onset_s, offset_s, mouse_id가 있는 별도 검증 데이터로 coverage/accuracy 평가"
        )
        self.ui.verticalLayout_5.addWidget(self.validation_button)
        self.validation_result = QLabel("Validation: not performed", self.ui.settings_panel)
        self.validation_result.setWordWrap(True)
        self.ui.verticalLayout_5.addWidget(self.validation_result)
        self.export_button = QPushButton("Export results CSV", self.ui.settings_panel)
        self.export_button.setEnabled(False)
        self.ui.verticalLayout.addWidget(self.export_button)

    def _connect_signals(self):
        self.ui.btn_audio_browse.setText("Browse")
        self.ui.btn_audio_browse.clicked.connect(
            lambda checked=False: self.open_audio_channel(0)
        )
        self.ui.btn_audio_browse_2.clicked.connect(
            lambda checked=False: self.open_audio_channel(1)
        )
        self.ui.btn_video_browse.clicked.connect(self.open_video)
        self.ui.btn_label_browse.clicked.connect(self.open_label)
        # 최신 원본 UI에서 추적파일 Browse의 objectName은 lbl_tracking_browse.
        self.ui.lbl_tracking_browse.clicked.connect(self.open_tracking)
        self.ui.btn_run_analysis.clicked.connect(self.run_analysis)
        self.ui.btn_show_default.clicked.connect(lambda: self.show_result_overlay("ratio"))
        self.ui.btn_distance_corr.clicked.connect(lambda: self.run_test_analysis("distance"))
        self.ui.btn_geo_corr.clicked.connect(lambda: self.run_test_analysis("geometry"))
        self.ui.btn_dist_show.clicked.connect(lambda: self.show_result_overlay("distance"))
        self.ui.btn_geo_show.clicked.connect(lambda: self.show_result_overlay("geometry"))
        self.ui.btn_save_process.clicked.connect(self.save_process)
        self.ui.btn_open_file.clicked.connect(self.open_process)
        self.ui.btn_save_pdf.clicked.connect(self.save_pdf)
        # 최신 Designer 상단의 CSV 저장과 분석 탭의 Plot 버튼을 연결한다.
        self.ui.btn_plot_spectrogram.clicked.connect(self._redraw_current_window)
        self.ui.btn_save_csv.clicked.connect(self.export_results)
        self.ui.btn_play_all.clicked.connect(self.toggle_video)
        # Separate playback buttons. Sync buttons remain marker-only controls.
        self.btn_audio_play.clicked.connect(self.toggle_audio_preview)
        self.btn_video_play.clicked.connect(self.toggle_video_only)
        self.audio_channel_select.currentIndexChanged.connect(
            self._on_audio_channel_selected
        )
        self.ui.slider_timeline_audio.sliderMoved.connect(
            self._on_audio_timeline_seek
        )
        self.audio_preview_player.positionChanged.connect(
            self._on_audio_preview_position
        )
        self.audio_preview_player.mediaStatusChanged.connect(
            self._on_audio_preview_media_status
        )
        self.audio_preview_player.errorOccurred.connect(
            self._on_audio_preview_error
        )
        self.player.playbackStateChanged.connect(
            self._update_video_play_buttons
        )
        self.ui.slider_timeline_audio.valueChanged.connect(self.draw_audio)
        self.ui.slider_timeline_video.sliderMoved.connect(self.player.setPosition)
        # 공통 타임라인은 두 Sync 기준점이 있으면 "동기화 시작점 이후 시간"을 의미.
        self.ui.slider_timeline_all.sliderMoved.connect(self._seek_common_timeline)
        self.player.durationChanged.connect(self._on_video_duration)
        self.player.positionChanged.connect(self.video_position)
        self.player.errorOccurred.connect(self._on_video_playback_error)

        self.ui.spin_n_fft.valueChanged.connect(self._redraw_current_window)
        self.ui.spin_n_fft.valueChanged.connect(self._invalidate_results)
        self.ui.spin_hop_length.valueChanged.connect(self._redraw_current_window)
        self.ui.spin_hop_length.valueChanged.connect(
            self._on_hop_length_changed
        )
        self.color_min_spin.valueChanged.connect(self._on_color_limits_changed)
        self.color_max_spin.valueChanged.connect(self._on_color_limits_changed)
        # Sync 버튼은 상대 매체로 탐색하는 대신 현재 기준점을 고정한다.
        self.ui.btn_starting_audio.clicked.connect(self.mark_audio_start)
        self.ui.btn_starting_video.clicked.connect(self.mark_video_start)
        self.reset_sync_button.clicked.connect(self.reset_sync_starts)
        self._refresh_sync_labels()
        self.geometry_button.clicked.connect(self.open_geometry)
        self.anchor_button.clicked.connect(self.review_anchor)
        self._update_audio_video_mapping_status()
        self.training_button.clicked.connect(self.fit_training_threshold)
        self.validation_button.clicked.connect(self.validate_results)
        self.export_button.clicked.connect(self.export_results)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def add_audio_channel(self, checked=False, fixed=False):
        """Audio input 행 추가. 앞의 두 줄은 삭제 불가능하고 추가분만 x 표시."""
        if len(self.audio_rows) >= 16:
            QMessageBox.information(self, "Audio channels", "최대 16개 마이크까지 지원합니다.")
            return
        index = len(self.audio_rows)
        panel = QWidget(self.channel_host)
        row_layout = QHBoxLayout(panel)
        row_layout.setContentsMargins(1, 1, 1, 1)
        # 동적으로 추가한 행도 고정된 Ch1/Ch2와 동일한 필드 순서를 갖는다.
        subject_label = QLabel("Subject ID", panel)
        label = QLabel(f"Ch {index+1} File Path", panel)
        subject = QLineEdit(panel)
        subject.setObjectName(f"subject_id_{index+1}")
        subject.setPlaceholderText("Subject ID")
        subject.setText(f"mouse_{index+1}")
        subject.setMaximumWidth(110)
        path = QLineEdit(panel)
        path.setObjectName(f"line_audio_path_{index+1}")
        path.setPlaceholderText("Select WAV / CSV")
        path.setReadOnly(True)
        browse = QPushButton("Browse", panel)

        delete = None
        if index >= 2 and not fixed:
            # 일부 글꼴/스타일에서 Unicode 곱셈기호(×)가 점처럼 렌더링된다.
            # 일반 ASCII 대문자 X를 사용하고 별도 QSS로 muted-red 버튼 지정.
            delete = QPushButton("X", panel)
            delete.setObjectName("btn_remove_audio_channel")
            delete.setToolTip(f"Remove Ch {index+1}")
            delete.setAccessibleName(f"Remove audio channel {index+1}")
            delete.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            delete.setFixedSize(30, 30)
            # Per-widget QSS has precedence over the application stylesheet.
            # Thus muted red still renders if the local ui/style.qss is outdated.
            # Never apply this style to the fixed Ch1/Ch2 rows.
            delete.setStyleSheet("""
                QPushButton {
                    background-color: #38292D;
                    color: #D39DA3;
                    border: 1px solid #61434A;
                    border-radius: 5px;
                    padding: 0px;
                    min-height: 0px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #483135;
                    color: #F0C2C6;
                    border-color: #875D65;
                }
                QPushButton:pressed {
                    background-color: #55383F;
                    border-color: #9B6A72;
                }
                QPushButton:disabled {
                    background-color: #292729;
                    color: #7C686B;
                    border-color: #3D3436;
                }
            """)

        # The delete button must be the LEFTMOST control for added rows.
        # Ch1/Ch2 are built in Qt Designer and have no delete button.
        if delete is not None:
            row_layout.addWidget(delete)
        row_layout.addWidget(subject_label)
        row_layout.addWidget(subject)
        row_layout.addWidget(label)
        row_layout.addWidget(path, stretch=1)
        row_layout.addWidget(browse)

        record = {"panel": panel, "subject_label": subject_label,
                  "label": label, "subject": subject,
                  "path": path, "browse": browse, "delete": delete}
        browse.clicked.connect(
            lambda checked=False, current=record:
                self.open_audio_channel(self.audio_rows.index(current))
        )
        if delete:
            delete.clicked.connect(
                lambda checked=False, current=record:
                    self.remove_audio_channel(self.audio_rows.index(current))
            )
        subject.editingFinished.connect(self._on_audio_subject_changed)
        self.channel_layout.insertWidget(self.channel_layout.count()-1, panel)
        self.audio_rows.append(record)
        self.audio_slots.append(None)
        # Every added WAV input owns one matching label-file input.
        if hasattr(self, "label_rows"):
            self.label_paths.append(None)
            self.label_specs.append(None)
            self.label_events.append([])
            self.audio_csv_specs.append(None)
            self._append_label_row()
            self._reflow_label_grid()
            self._recompute_label_events()
        self._sync_channel_fields()
        if len(self.audio_rows) > 2:
            self._on_channel_structure_changed()

    def remove_audio_channel(self, index):
        """첫 두 줄은 보존. 그 뒤의 행만 삭제하고 채널 인덱스 재지정."""
        if index < 2 or index >= len(self.audio_rows):
            return
        record = self.audio_rows.pop(index)
        self.audio_slots.pop(index)
        self.channel_layout.removeWidget(record["panel"])
        record["panel"].deleteLater()
        if hasattr(self, "label_rows"):
            label_row = self.label_rows.pop(index)
            for key in ("label", "path", "browse"):
                self.ui.gridLayout_2.removeWidget(label_row[key])
                label_row[key].deleteLater()
            self.label_paths.pop(index)
            self.label_specs.pop(index)
            self.label_events.pop(index)
            self.audio_csv_specs.pop(index)
            self._reflow_label_grid()
            self._recompute_label_events()
        self._sync_channel_fields()
        self._on_channel_structure_changed()

    def _sync_channel_fields(self):
        """화면 순서와 AudioData.channel_id를 항상 같은 순서로 유지."""
        self.channel_fields = [entry["path"] for entry in self.audio_rows]
        self.subject_fields = [entry["subject"] for entry in self.audio_rows]
        for i, (row, audio) in enumerate(zip(self.audio_rows, self.audio_slots)):
            # 최초 2개/추가/중간 행 삭제 후에도 라벨 표기를 항상 통일한다.
            row["subject_label"].setText("Subject ID")
            row["label"].setText(f"Ch {i+1} File Path")
            if i >= 2:
                # Qt Designer에서 만든 1, 2번 Subject ID 위젯 이름은 보존한다.
                row["subject"].setObjectName(f"subject_id_{i+1}")
            if row["delete"] is not None:
                row["delete"].setToolTip(f"Remove Ch {i+1}")
                row["delete"].setAccessibleName(f"Remove audio channel {i+1}")
            if audio is not None:
                audio.channel_id = f"ch{i+1}"

    def _on_channel_structure_changed(self):
        """Changing audio channel order/count invalidates explicit video associations."""
        self.anchor_mapping = None
        self._update_audio_video_mapping_status()
        if hasattr(self, "label_geometry"):
            self.geometry = None
            self.label_geometry.setText("Geometry: none (channel list changed)")
            self._clear_sync_starts()
        if hasattr(self, "btn_audio_play"):
            self._stop_audio_preview()
            self._update_audio_playback_channels()
        if hasattr(self, "graph_layout"):
            self._rebuild_plots()
            self._refresh_audio_state()
            self.draw_audio(0, force=True)

    def _update_audio_video_mapping_status(self):
        """Show confirmed channel→Subject ID→SLEAP Track ID associations."""
        if not hasattr(self, "label_anchor_mapping"):
            return
        if not self.anchor_mapping:
            self.label_anchor_mapping.setText(
                "Audio ↔ Video mapping: not confirmed"
            )
            return
        try:
            subjects = self.channel_subjects()
            validate_audio_video_mapping(self.anchor_mapping, subjects)
            reverse = {
                mouse: track for track, mouse in
                self.anchor_mapping["track_to_mouse"].items()
            }
            self.label_anchor_mapping.setText(
                "Audio ↔ Video:\n" + "\n".join(
                    f"{ch} · {mouse} ↔ {reverse[mouse]}"
                    for ch, mouse in subjects.items()
                )
            )
        except (ValueError, KeyError) as exc:
            self.label_anchor_mapping.setText(
                f"Audio ↔ Video mapping invalid: {exc}"
            )

    def _on_audio_subject_changed(self):
        """Audio Subject ID edit invalidates stale mouse↔track associations."""
        self._invalidate_results()
        if self.anchor_mapping:
            try:
                validate_audio_video_mapping(
                    self.anchor_mapping, self.channel_subjects()
                )
            except (ValueError, KeyError):
                self.anchor_mapping = None
        self._update_audio_video_mapping_status()

    def channel_subjects(self):
        """사용자가 입력한 실험체 ID 매핑; empirical A/B는 두 개의 서로 다른 ID 필수."""
        ids = [f.text().strip() for f in self.subject_fields]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ValueError("Subject ID는 비어 있거나 중복될 수 없습니다.")
        return {f"ch{i+1}": mid for i, mid in enumerate(ids)}


    def _rebuild_plots(self):
        """스펙트로그램 위젯 개수는 항상 마이크 개수와 같게 유지."""
        self._clear_layout(self.graph_layout)
        self.overlay_items = []
        self.spectrogram_views = []
        self.audio_anchor_lines = []
        for index, audio in enumerate(self.audio_slots):
            # Channel separators are subtle; the plot fills the whole card
            # rather than sitting inside several padded frames.
            panel = QWidget(self.graph_host)
            panel.setObjectName("spectrogram_channel_panel")
            panel.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(2, 1, 2, 2)
            layout.setSpacing(2)
            title = f"ch{index+1} | {audio.path.name if audio else 'No WAV'}"
            title_label = QLabel(title, panel)
            title_label.setObjectName("spectrogram_channel_title")
            title_label.setFixedHeight(20)
            layout.addWidget(title_label)

            # QSS does not own GraphicsView's plot background: set it here.
            plot = pg.PlotWidget(panel)
            plot.setObjectName("spectrogram_plot")
            plot.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            # Two channels should fit on a normal-height workspace; on a larger
            # window each graph expands to use the spare height.
            plot.setMinimumHeight(155)
            plot.setFrameShape(QFrame.Shape.NoFrame)
            plot.setBackground(SPECTROGRAM_BG)
            plot.setLabel("bottom", "Time", units="s")
            plot.setLabel("left", "Frequency", units="Hz")
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(SPECTROGRAM_AXIS_LINE))
                axis.setTextPen(pg.mkPen(SPECTROGRAM_AXIS))
            # With no WAV loaded, don't show misleading -25..25 default axes.
            # The empty placeholder remains the same charcoal as the parent.
            has_audio = audio is not None
            plot.showAxis("left", show=has_audio)
            plot.showAxis("bottom", show=has_audio)
            plot.showGrid(x=has_audio, y=has_audio, alpha=0.11)
            if not has_audio:
                # 초기 PyQtGraph의 -25..25 축을 보여주는 대신 같은 차콜 바탕의
                # 작은 안내 문구만 표시. 데이터 로드시 그래프를 다시 구성한다.
                plot.setXRange(0, 1, padding=0)
                plot.setYRange(0, 1, padding=0)
                empty_text = pg.TextItem(
                    text="WAV 파일을 불러오면 스펙트로그램이 표시됩니다.",
                    color="#8F9BA2", anchor=(0.5, 0.5)
                )
                empty_text.setPos(0.5, 0.5)
                plot.addItem(empty_text, ignoreBounds=True)
            image = pg.ImageItem(axisOrder="col-major")
            image.setLookupTable(self.spectrogram_lut)
            image.setLevels(self.spectrogram_levels_db)  # 생성부터 고정 레벨 적용
            plot.addItem(image)
            # 동기화 기준점(시안)과 현재 재생 위치(앰버)를 구분.
            playhead = pg.InfiniteLine(
                angle=90, pen=pg.mkPen(SPECTROGRAM_PLAYHEAD, width=1.6),
                movable=False
            )
            plot.addItem(playhead)
            playhead.setVisible(self._sync_is_ready())
            # 초록색 점선은 고정한 오디오 기준점으로, 타임라인 이동에 영향을 받지 않는다.
            anchor_line = pg.InfiniteLine(
                angle=90, pen=pg.mkPen(SPECTROGRAM_SYNC_MARKER, width=2,
                                      style=Qt.PenStyle.DashLine), movable=False
            )
            plot.addItem(anchor_line)
            anchor_line.setVisible(self.audio_start_s is not None)
            if self.audio_start_s is not None:
                anchor_line.setValue(self.audio_start_s)
            if self.spectrogram_views:
                plot.setXLink(self.spectrogram_views[0][0])
            self.spectrogram_views.append((plot, image, playhead))
            self.audio_anchor_lines.append(anchor_line)
            layout.addWidget(plot, 1)
            # Equal stretch makes N channel charts share the viewport height.
            # If N exceeds available space, QScrollArea handles vertical scroll.
            self.graph_layout.addWidget(panel, 1)
        # No trailing stretch: it previously consumed height without showing data.

    def _validate_slots(self, slots):
        loaded = [a for a in slots if a is not None]
        if len({a.path.resolve() for a in loaded}) != len(loaded):
            raise ValueError("중복 WAV 파일이 서로 다른 채널에 들어 있습니다.")
        if len({a.sr for a in loaded}) > 1:
            raise ValueError("모든 WAV sampling rate가 일치해야 합니다.")
        # 물리적 녹음 시작 시간이 같은지 여부는 파일 길이/샘플레이트로 확인 불가.

    def open_all_audio(self):
        """보조 기능: 전체 마이크 수만큼 WAV를 한 번에 선택하여 적용."""
        count = len(self.audio_rows)
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Select {count} WAV files", "", "WAV (*.wav)"
        )
        if not paths:
            return
        if len(paths) != count:
            QMessageBox.warning(self, "Audio", f"WAV 파일 {count}개가 필요합니다.")
            return
        try:
            candidate = [load_audio(path, f"ch{i+1}")
                         for i, path in enumerate(paths)]
            self._validate_slots(candidate)
            self.audio_slots = candidate
            self.audio_csv_specs = [None] * len(candidate)
            self._apply_audio_update()
        except Exception as exc:
            QMessageBox.critical(self, "Audio loading", str(exc))

    def open_audio_channel(self, index):
        """각 행의 Browse에서 파형 WAV/CSV 선택 후 그 행의 경로창만 갱신."""
        path, _ = QFileDialog.getOpenFileName(
            self, f"Audio for ch{index+1}", "",
            "Audio files (*.wav *.csv);;WAV (*.wav);;CSV (*.csv);;All files (*.*)"
        )
        if not path:
            return
        csv_sr, csv_spec = None, None
        if Path(path).suffix.lower() == ".csv":
            csv_sr, accepted = QInputDialog.getInt(
                self, "Sampling rate", "CSV waveform sampling rate (Hz):",
                250000, 1, 2000000, 1
            )
            if not accepted:
                return
            try:
                review = CSVHeaderDialog(
                    path, kind="waveform", sampling_rate=csv_sr, parent=self
                )
                if review.exec() != QDialog.DialogCode.Accepted:
                    return
                csv_spec = review.result_spec
            except Exception as error:
                QMessageBox.warning(self, "Waveform CSV header", str(error))
                return
        try:
            updated = list(self.audio_slots)
            updated[index] = load_audio(
                path, f"ch{index+1}", csv_sr=csv_sr, csv_spec=csv_spec
            )
            self._validate_slots(updated)
            self.audio_slots = updated
            self.audio_csv_specs[index] = csv_spec
            self._apply_audio_update()
        except Exception as error:
            QMessageBox.critical(self, "Audio loading", str(error))

    def _apply_audio_update(self):
        # File replacement invalidates an ongoing audible preview of old samples.
        self._stop_audio_preview()
        self._update_audio_playback_channels()
        for i, audio in enumerate(self.audio_slots):
            self.channel_fields[i].setText(str(audio.path) if audio else "")
        self.results = []
        self.export_button.setEnabled(False)
        if getattr(self, "label_paths", None):
            try:
                self._reload_label_events_for_audio()
            except Exception as error:
                self.events = []
                self._invalidate_results()
                QMessageBox.warning(self, "Label/WAV mismatch", str(error))
        else:
            self._invalidate_results()
        self._clear_sync_starts()
        self._rebuild_plots()
        self._refresh_audio_state()
        self.draw_audio(0, force=True)

    def _refresh_audio_state(self):
        loaded = [a for a in self.audio_slots if a is not None]
        # ch1 경로창은 전체 상태 요약으로 덮어쓰지 않는다.
        self.statusBar().showMessage(
            f"Audio: {len(loaded)}/{len(self.audio_slots)} channels loaded", 3000
        )
        duration = min((a.duration_s for a in loaded), default=0.0)
        # Audio slider represents absolute PLAYHEAD seconds, not the left edge
        # of the 3s spectrogram window. Previous range duration-3s prevented
        # the slider from moving at all for recordings <=3 seconds.
        total_ms = max(0, int(round(duration * 1000)))
        with QSignalBlocker(self.ui.slider_timeline_audio):
            self.ui.slider_timeline_audio.setRange(0, total_ms)
            self.ui.slider_timeline_audio.setValue(0)
        self.window_start_ms = 0
        self._audio_cursor_ms = 0
        self.ui.lbl_time_audio.setText(
            f"{self._format_ms(0)} / {self._format_ms(total_ms)}"
        )

    def _on_color_limits_changed(self, _=None):
        """고정 범위를 사용자가 변경하면 STFT 재계산 없이 색상만 다시 매핑.

        slider/playback 이동에서는 호출되지 않으므로 색상 기준은 유지된다.
        모든 마이크와 모든 시간 구간에 동일한 (min_db, max_db)를 적용한다.
        """
        minimum = float(self.color_min_spin.value())
        maximum = float(self.color_max_spin.value())
        if minimum >= maximum:
            self.statusBar().showMessage(
                "Invalid color scale: Min must be smaller than Max.", 5000
            )
            return  # 올바른 범위가 입력될 때까지 이전 스케일 보존.

        self.spectrogram_levels_db = (minimum, maximum)
        for _, image, _ in self.spectrogram_views:
            image.setLevels(self.spectrogram_levels_db)

        self.color_scale_info.setText(
            f"Fixed for all channels/time: {minimum:g} to {maximum:g} dB rel."
        )

    def _redraw_current_window(self, _=None):
        self.draw_audio(self.ui.slider_timeline_audio.value(), force=True)

    def draw_audio(self, position_ms, force=False):
        """Absolute playhead in milliseconds; redraw STFT only on window shift.

        For a 2s audio the timeline range is 0..2000ms (NOT 0..0ms).
        The waveform cursor can advance at 50ms intervals while the
        expensive 3s STFT is only regenerated when the visible window changes.
        """
        total_ms = self.ui.slider_timeline_audio.maximum()
        head_ms = max(0, min(int(position_ms), total_ms))
        self._audio_cursor_ms = head_ms
        self.ui.lbl_time_audio.setText(
            f"{self._format_ms(head_ms)} / {self._format_ms(total_ms)}"
        )
        for _, _, playhead in self.spectrogram_views:
            playhead.setValue(head_ms / 1000)
            playhead.setVisible(total_ms > 0)

        window_ms = int(round(self.VIEW_SECONDS * 1000))
        window_end_ms = self.window_start_ms + window_ms
        need_redraw = (
            force or head_ms < self.window_start_ms
            or head_ms >= self.window_start_ms + int(window_ms * 0.85)
        )
        if not need_redraw:
            return
        max_start = max(0, total_ms - window_ms)
        new_start = min(
            max_start, max(0, head_ms - int(window_ms * 0.20))
        )
        if new_start == self.window_start_ms and not force:
            return
        self.window_start_ms = new_start
        self.render_id += 1
        self.render_timer.start()

    def _start_render(self):
        if self.render_busy:
            return  # 이전 worker 완료 시 최신 render_id로 다시 예약.
        if not any(audio is not None for audio in self.audio_slots):
            return
        self.render_busy = True
        job = ComputeJob(
            self.render_id, compute_spectrograms, tuple(self.audio_slots),
            self.window_start_ms / 1000.0, self.VIEW_SECONDS,
            self.ui.spin_n_fft.value(), self.ui.spin_hop_length.value()
        )
        job.signals.finished.connect(self._render_finished)
        self.pool.start(job)

    def _render_finished(self, job_id, rendered, error):
        self.render_busy = False
        if job_id != self.render_id:
            self.render_timer.start(0)  # 오직 가장 최신 시각의 그래프만 표시.
            return
        if error:
            self.statusBar().showMessage(f"STFT: {error}", 5000)
            return
        if not rendered or len(rendered) != len(self.spectrogram_views):
            return

        # 절대 동일한 로그레벨은 타임라인 위치와 마이크 채널에 무관하게 같은 색이다.
        # 표시창마다 percentile로 범위를 새로 추정하지 않는다.
        low, high = self.spectrogram_levels_db

        for index, entry in enumerate(rendered):
            plot, image, playhead = self.spectrogram_views[index]
            if entry is None or not entry[0].size:
                image.clear()
                continue
            db, t, f = entry
            image.setImage(db.T, autoLevels=False, levels=(float(low), float(high)))
            sr = self.audio_slots[index].sr
            hop = self.ui.spin_hop_length.value()
            n_fft = self.ui.spin_n_fft.value()
            dt = t[1] - t[0] if len(t) > 1 else hop / sr
            df = f[1] - f[0] if len(f) > 1 else sr / n_fft
            image.setRect(QRectF(t[0]-dt/2, f[0]-df/2, len(t)*dt, len(f)*df))
            plot.setYRange(float(f[0]), float(f[-1]), padding=0)
            # The spectrogram cursor is always the CURRENT audio slider time,
            # regardless of preview mode or whether Sync markers are assigned.
            # Rendering a new window must never hide or rewind the playhead.
            playhead.setVisible(
                self.ui.slider_timeline_audio.maximum() > 0
            )
            playhead.setValue(self._audio_cursor_ms / 1000.0)

        start_s = self.window_start_ms / 1000
        if self.spectrogram_views:
            self.spectrogram_views[0][0].setXRange(
                start_s, start_s+self.VIEW_SECONDS, padding=0
            )
        # Rendering a new STFT window must never reset the PLAYHEAD clock.
        self.ui.lbl_time_audio.setText(
            f"{self._format_ms(self._audio_cursor_ms)} / "
            f"{self._format_ms(self.ui.slider_timeline_audio.maximum())}"
        )
        self._refresh_result_overlay()
        if getattr(self, "active_overlay_mode", None):
            self._refresh_result_overlay()

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Video", "", "Video (*.mp4 *.avi *.mov)"
        )
        if path:
            self._play_all_active = False
            self.player.stop()
            self.ui.line_video_path.setText(path)
            self._clear_sync_starts()
            self.player.setSource(QUrl.fromLocalFile(path))
            self.has_video = True
            self.ui.btn_play_all.setEnabled(True)
            self.btn_video_play.setEnabled(True)
            self._update_video_play_buttons()
            self.ui.tab_content.setCurrentWidget(self.ui.tab_video)
            # 다른 영상을 선택하면 기존 tracking/anchor가 맞지 않으므로 해제.
            self.tracking_df = None
            self.anchor_mapping = None
            self._update_audio_video_mapping_status()
            self.ui.line_tracking_file.clear()

    def open_label(self, index=0):
        """각 Ch별 Label CSV/TXT 선택 → 헤더/열/단위 확인 → 독립 저장.

        기존 단일 Label Browse는 Ch1에 연결되며 추가된 Label Browse는
        대응 Ch2..ChN 파일만 바꾼다. 전체 분석 이벤트는 N개 파일의
        중복된 USV를 merge_channel_events()로 모아 하나씩만 분석한다.
        """
        if isinstance(index, bool):
            index = 0  # QPushButton.clicked(bool)를 안전하게 무시.
        if not 0 <= int(index) < len(self.label_rows):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"USV labels for Ch {index+1}", "",
            "Label files (*.csv *.txt);;CSV (*.csv);;Text (*.txt);;All (*.*)"
        )
        if not path:
            return
        try:
            audio = self.audio_slots[index]
            review = CSVHeaderDialog(
                path, kind="labels",
                sampling_rate=audio.sr if audio is not None else None,
                hop_length=self.ui.spin_hop_length.value(),
                parent=self
            )
            if review.exec() != QDialog.DialogCode.Accepted:
                return
            spec = review.result_spec
            loaded = load_labels(
                path, table_spec=spec,
                sr=audio.sr if audio is not None else None,
                hop_length=self.ui.spin_hop_length.value()
            )
            if audio is not None and any(
                event.offset_s > audio.duration_s for event in loaded
            ):
                raise ValueError(
                    f"Label times extend beyond Ch {index+1} WAV "
                    f"({audio.duration_s:.3f} s). Check chosen time unit."
                )
            # Commit path+spec only after the entire file is parsed successfully.
            self.label_paths[index] = str(path)
            self.label_specs[index] = spec
            self.label_events[index] = loaded
            self.label_rows[index]["path"].setText(str(path))
            self.label_rows[index]["path"].setToolTip(str(path))
            self._recompute_label_events()
        except Exception as error:
            QMessageBox.critical(self, "USV label header/import", str(error))

    def open_tracking(self):
        """SLEAP instances CSV 또는 추적 결과 .slp를 읽어 좌표를 준비.

        .slp는 '모델 가중치'가 아니라 추적 결과 파일일 수 있으므로 CLI export
        호출을 시도한다. 실제 CLI 버전/환경이 없으면 오류 메시지를 보여준다.
        """
        if not self.has_video:
            QMessageBox.warning(self, "Video required", "먼저 영상을 선택하세요.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "SLEAP tracking", "",
            "SLEAP results (*.slp *.csv);;SLP (*.slp);;CSV (*.csv);;All (*.*)"
        )
        if not path:
            return
        try:
            import cv2
            from video.track_run import load_tracking_table
            cap = cv2.VideoCapture(self.ui.line_video_path.text())
            if not cap.isOpened():
                raise ValueError("Cannot read video FPS.")
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()
            csv_spec = None
            if Path(path).suffix.lower() == ".csv":
                review = CSVHeaderDialog(path, kind="tracking", parent=self)
                if review.exec() != QDialog.DialogCode.Accepted:
                    return
                csv_spec = review.result_spec
                raw = read_delimited_table(path, csv_spec)
            else:
                raw = load_tracking_table(path)  # .slp -> exported instances CSV
            tracked = process_tracking_data(raw, fps)
            self.tracking_df = tracked
            self.tracking_path = path
            self.tracking_csv_spec = csv_spec
            self.anchor_mapping = None
            self._invalidate_results()
            self._update_audio_video_mapping_status()
            self.ui.line_tracking_file.setText(path)
            self.statusBar().showMessage(
                f"Tracking: {len(tracked)} rows, FPS={fps:.3f}", 6000
            )
            self.review_anchor()
        except Exception as error:
            QMessageBox.critical(self, "Tracking error", str(error))

    def review_anchor(self):
        """Explicit Audio channel/Subject ID ↔ SLEAP Video track selection.

        The preview renders SLEAP track IDs on the selected frame. For each
        Audio Subject ID the user must choose which track represents that
        same physical mouse. Never prefill from track order or filename.
        The mapping is saved as track_to_mouse + mouse_to_channel.
        """
        if self.tracking_df is None:
            QMessageBox.information(
                self, "Audio ↔ Video mapping", "SLEAP tracking CSV/.slp를 먼저 불러오세요."
            )
            return
        if not self.has_video:
            QMessageBox.information(
                self, "Audio ↔ Video mapping", "추적에 사용한 영상을 먼저 불러오세요."
            )
            return
        try:
            subjects = self.channel_subjects()
            if len(self.audio_slots) != len(subjects):
                raise ValueError("One distinct Subject ID is needed per audio channel.")
            frame_id = select_anchor_frame(self.tracking_df)
            frame_bgr = draw_anchor_frame(
                self.ui.line_video_path.text(), self.tracking_df, frame_id
            )
            rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
            height, width, _ = rgb.shape
            qimage = QImage(
                rgb.data, width, height, rgb.strides[0],
                QImage.Format.Format_RGB888
            ).copy()
            rows = self.tracking_df[self.tracking_df.frame_idx == frame_id]
            track_ids = sorted(
                str(x) for x in rows.track_id.dropna().unique()
                if str(x).strip()
            )
            if len(track_ids) < len(subjects):
                raise ValueError(
                    f"Anchor frame #{frame_id} has only {len(track_ids)} tracks "
                    f"for {len(subjects)} audio subjects. "
                    "Select a frame showing all mice or fix tracking first."
                )
        except Exception as error:
            QMessageBox.critical(self, "Audio ↔ Video mapping", str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Confirm Audio ↔ Video subjects — frame #{frame_id}")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Match each Audio Subject ID with the SLEAP Track ID shown in "
            "the video preview. The track order is NOT assumed to match "
            "audio channel order.", dialog
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        preview = QLabel(dialog)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setPixmap(QPixmap.fromImage(qimage).scaled(
            960, 440, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))
        layout.addWidget(preview)
        form = QFormLayout()
        choices = {}
        existing = (self.anchor_mapping or {}).get("track_to_mouse", {})
        for channel_id, subject_id in subjects.items():
            combo = QComboBox(dialog)
            combo.addItem("— Select video Track ID —", None)
            for track_id in track_ids:
                combo.addItem(track_id, track_id)
            old_track = next(
                (track for track, mouse in existing.items()
                 if mouse == subject_id and track in track_ids), None
            )
            if old_track is not None:
                combo.setCurrentIndex(combo.findData(old_track))
            combo.setToolTip(
                f"Physical mouse {subject_id} recorded as Audio {channel_id}"
            )
            form.addRow(f"{channel_id} | {subject_id} → Video track", combo)
            choices[channel_id] = combo
        layout.addLayout(form)
        info = QLabel(
            "Each track can be selected once. Unselected extra video tracks "
            "are ignored; the assignment model requires matched subjects.",
            dialog
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel, dialog
        )

        def check_and_accept():
            selected = [choice.currentData() for choice in choices.values()]
            if any(track is None for track in selected):
                QMessageBox.warning(
                    dialog, "Incomplete mapping", "Select a track for every Audio Subject ID."
                )
                return
            if len(set(selected)) != len(selected):
                QMessageBox.warning(
                    dialog, "Duplicate track", "Each Audio Subject ID needs a DIFFERENT video track."
                )
                return
            dialog.accept()

        buttons.accepted.connect(check_and_accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            track_to_mouse = {
                choices[channel_id].currentData(): subject
                for channel_id, subject in subjects.items()
            }
            mouse_to_channel = {
                subject: channel_id for channel_id, subject in subjects.items()
            }
            mapping = save_anchor_mapping(track_to_mouse, mouse_to_channel)
            validate_audio_video_mapping(mapping, subjects)
            self.anchor_mapping = mapping
            self._invalidate_results()
            self._update_audio_video_mapping_status()
            self.statusBar().showMessage(
                f"Confirmed {len(mapping['track_to_mouse'])} Audio ↔ Video matches.",
                8000
            )
        except Exception as error:
            QMessageBox.critical(self, "Audio ↔ Video mapping", str(error))


    def open_geometry(self):
        """실측 위치·몸체 크기·경험적 모델 파라미터·학습된 경계를 JSON으로 읽음.

        필수 JSON 예:
        {
          "mic_positions_m":{"ch1":[x1,y1],"ch2":[x2,y2]},
          "pixel_to_meter_h":[[h00,h01,h02],[h10,h11,h12],[h20,h21,h22]],
          "empirical":{"body_length_m":L,"body_width_m":W,
                       "beta":1,"alpha_theta":1,"omega_min":0.3},
          "threshold":{"r_mid":TRAIN_MEDIAN,"mad":TRAIN_MAD,"k":1}
        }
        예시 문자열 L/W/좌표/median은 실제 측정값으로 대체해야 한다.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Validated geometry + threshold", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            geo = load_geometry_json(path)
            required = {f"ch{i+1}" for i in range(len(self.audio_slots))}
            if geo.mic_positions_m and set(geo.mic_positions_m) != required:
                raise ValueError("mic_positions_m keys must match channel IDs.")
            with open(path, "r", encoding="utf-8") as handle:
                obj = json.load(handle)
            emp = None
            threshold = None
            if "empirical" in obj:
                emp = EmpiricalGeoConfig(**obj["empirical"])
            if "threshold" in obj:
                threshold = EmpiricalThreshold(**obj["threshold"])
            self.geometry = geo
            self.empirical_cfg = emp
            self.empirical_threshold = threshold
            self.geometry_path = path
            self._invalidate_results()
            configured = bool(emp is not None and threshold is not None)
            self.label_geometry.setText(
                f"Geometry: {Path(path).name} | empirical: "
                f"{'ready' if configured else 'missing config/threshold'}"
            )
            self.statusBar().showMessage(
                "Calibration loaded; validation quality is NOT established by parsing JSON.",
                8000
            )
        except Exception as error:
            QMessageBox.critical(self, "Geometry", str(error))

    def _sync_is_ready(self):
        """오디오·영상에 동일 사건 기준점을 모두 지정했는가?"""
        return self.audio_start_s is not None and self.video_start_s is not None

    def _invalidate_results(self):
        """동기화/입력이 변경되면 이전 결과와 진행 중인 계산을 무효화한다."""
        self.analysis_generation += 1
        self.results = []
        self.analysis_results = {"ratio": [], "distance": [], "geometry": []}
        self.validation_metrics = {}
        if hasattr(self, "validation_result"):
            self.validation_result.setText("Validation: not performed")
        self.active_overlay_mode = None
        self.export_button.setEnabled(False)
        self.ui.text_result.clear()
        self.ui.text_dist_result.clear()
        self.ui.text_geo_result.clear()
        if self.overlay_items:
            for plot, item in self.overlay_items:
                try:
                    plot.removeItem(item)
                except RuntimeError:
                    pass
            self.overlay_items = []

    def _clear_sync_starts(self):
        """새 미디어 입력 시 고정 시작점을 해제하고 offset을 초기화한다."""
        self.audio_start_s = None
        self.video_start_s = None
        self.sync = SyncConfig()
        self._invalidate_results()
        if hasattr(self, "audio_anchor_lines"):
            for line in self.audio_anchor_lines:
                line.setVisible(False)
        if hasattr(self, "spectrogram_views"):
            for _, _, playhead in self.spectrogram_views:
                playhead.setVisible(False)
        self._refresh_sync_labels()
        # Sync를 해제하면 하단 공통 타임라인의 범위도 전체 영상 길이로 복구.
        if hasattr(self, "player"):
            self._on_video_duration(self.video_duration_ms)

    def reset_sync_starts(self):
        """명시적으로 Reset을 누른 경우에만 두 고정 시작점을 수동 해제."""
        self._clear_sync_starts()
        self.statusBar().showMessage("Audio/Video Sync 기준점을 해제했습니다.", 5000)

    def _refresh_sync_labels(self):
        """탭별 고정 시각·버튼 상태·계산된 offset을 동시에 보여준다."""
        if not hasattr(self, "label_sync_state"):
            return
        a = "—" if self.audio_start_s is None else f"{self.audio_start_s:.3f}s"
        v = "—" if self.video_start_s is None else f"{self.video_start_s:.3f}s"
        self.audio_start_label.setText(f"A: {a}")
        self.video_start_label.setText(f"V: {v}")
        # 사용자 탐색과 무관한 별도 고정 마커: 변경/초기화 시에만 좌표 갱신.
        self.audio_slider_marker.set_marker_ms(
            None if self.audio_start_s is None else self.audio_start_s * 1000
        )
        self.video_slider_marker.set_marker_ms(
            None if self.video_start_s is None else self.video_start_s * 1000
        )
        # 공통 타임라인의 원점(0)은 두 Sync marker가 가리키는 동일 사건.
        # Video 개별 타임라인은 절대 시간에, 공통 타임라인은 상대 시간 0에 표시.
        self.shared_slider_marker.set_marker_ms(
            0 if self._sync_is_ready() else None
        )
        self.ui.btn_starting_audio.setText("Sync ✓" if self.audio_start_s is not None else "Sync")
        self.ui.btn_starting_video.setText("Sync ✓" if self.video_start_s is not None else "Sync")
        self.ui.btn_starting_audio.setToolTip(
            "오디오 타임라인의 현재 재생 위치(초)를 Sync 기준점으로 고정/재지정."
        )
        self.ui.btn_starting_video.setToolTip("현재 영상 재생 헤드의 시각을 시작점으로 고정/재지정.")
        if self._sync_is_ready():
            self.label_sync_state.setText(
                f"Sync 고정: A={a}, V={v}, offset={self.sync.offset_s:+.3f}s"
            )
            self.offset_spin.setEnabled(True)
            with QSignalBlocker(self.offset_spin):
                self.offset_spin.setValue(self.sync.offset_s)
        else:
            self.label_sync_state.setText(f"Sync 대기: Audio={a} / Video={v} (양쪽 지정 필요)")
            self.offset_spin.setEnabled(False)
            with QSignalBlocker(self.offset_spin):
                self.offset_spin.setValue(0)

    def _recalculate_sync_from_anchors(self):
        """두 기준점을 동일 사건으로 놓고 offset을 산출; 이후에도 고정 유지."""
        self._invalidate_results()
        if self._sync_is_ready():
            self.sync = sync_from_start_points(self.audio_start_s, self.video_start_s)
        else:
            self.sync = SyncConfig()  # 단일 기준점으로는 정렬하지 않는다.
        self._refresh_sync_labels()
        self._on_video_duration(self.video_duration_ms)
        for line in self.audio_anchor_lines:
            line.setVisible(self.audio_start_s is not None)
            if self.audio_start_s is not None:
                line.setValue(self.audio_start_s)
        for _, _, cursor in self.spectrogram_views:
            cursor.setVisible(self._sync_is_ready())
        if self._sync_is_ready():
            # 새 고정 기준을 반영한 영상→오디오 커서를 즉시 갱신한다.
            self.video_position(self.player.position())
            self.statusBar().showMessage(
                f"시간축 정렬 완료: Audio={self.audio_start_s:.3f}s, "
                f"Video={self.video_start_s:.3f}s, offset={self.sync.offset_s:+.3f}s", 7000
            )

    def mark_audio_start(self):
        """Audio Sync: 현재 슬라이더 시각을 고정하고 초록색 세로 마커로 표시."""
        if not self.audio_slots or any(a is None for a in self.audio_slots):
            QMessageBox.information(self, "Audio Sync", "모든 WAV 파일을 먼저 불러오세요.")
            return
        # Audio 미리듣기 중에는 창의 왼쪽 끝이 아닌 실제 재생 커서를 고정.
        # 정지 상태에서는 사용자가 탐색해 놓은 Audio timeline 값을 사용한다.
        if self._audio_preview_active:
            self.audio_start_s = float(self._audio_preview_cursor_s)
        else:
            self.audio_start_s = self.ui.slider_timeline_audio.value() / 1000.0
        self._recalculate_sync_from_anchors()

    def mark_video_start(self):
        """Video Sync: 현재 영상 시각을 고정하고 주황색 세로 마커로 표시."""
        if not self.has_video:
            QMessageBox.information(self, "Video Sync", "비디오 파일을 먼저 불러오세요.")
            return
        self.video_start_s = self.player.position() / 1000.0
        self._recalculate_sync_from_anchors()


    def _on_hop_length_changed(self, _=None):
        """Frame-index label onsets depend on hop_length; reload on change."""
        if any(
            spec is not None
            and spec.get("time_unit") == "stft_frames"
            and spec.get("label_hop_length") is None  # legacy-only case
            for spec in getattr(self, "label_specs", [])
        ):
            try:
                self._reload_label_events_for_audio()
            except Exception as error:
                self.events = []
                self._invalidate_results()
                QMessageBox.warning(
                    self, "STFT frame labels", str(error)
                )
        else:
            self._invalidate_results()

    def _audio_event_preflight(self):
        """라벨 시간/샘플 수가 실제 WAV의 분석 가능 구간과 일치하는지 검사.

        True이면 분석 가능. 전체 이벤트가 WAV 구간 밖이거나 n_fft보다 짧다면
        무의미한 unassigned 목록을 출력하지 않고 원인을 사용자에게 알려준다.
        """
        loaded = [audio for audio in self.audio_slots if audio is not None]
        if not loaded or not self.events:
            return True
        duration = min(audio.duration_s for audio in loaded)
        sr = min(audio.sr for audio in loaded)
        n_fft = self.ui.spin_n_fft.value()
        outside = sum(event.onset_s >= duration or event.offset_s > duration
                      for event in self.events)
        too_short = sum((event.offset_s - event.onset_s) * sr < n_fft
                        for event in self.events)
        if outside == len(self.events):
            QMessageBox.warning(
                self, "USV label time scale mismatch",
                f"모든 라벨의 시간이 WAV 길이({duration:.3f}s) 밖입니다. "
                "CSV onset/offset이 초(s)가 맞는지, ms나 STFT frame index가 "
                "아닌지 확인하세요."
            )
            return False
        if too_short == len(self.events):
            QMessageBox.warning(
                self, "USV labels too short",
                f"모든 이벤트가 n_fft={n_fft} 샘플보다 짧아 분석할 수 없습니다. "
                "FFT 길이를 낮추거나 라벨의 시간 단위를 확인하세요."
            )
            return False
        if outside or too_short:
            self.statusBar().showMessage(
                f"Warning: out-of-WAV labels {outside}, "
                f"shorter-than-n_fft events {too_short} / {len(self.events)}.",
                12000
            )
        return True

    def run_analysis(self):
        """Default 탭: 영상과 무관하게 ratio-only 기준선 분석."""
        if self.analysis_busy:
            return
        if len(self.audio_slots) < 2 or any(a is None for a in self.audio_slots):
            QMessageBox.warning(self, "Audio", "오디오 파일이 모든 채널에 필요합니다.")
            return
        if len(self.label_paths) != len(self.audio_slots) or not all(self.label_paths):
            QMessageBox.warning(
                self, "USV labels",
                f"마이크 {len(self.audio_slots)}개에 대응하는 Label CSV/TXT "
                "파일을 채널별로 모두 선택해야 합니다."
            )
            return
        if not self.events:
            QMessageBox.warning(
                self, "USV labels",
                "모든 채널의 라벨을 통합했지만 분석할 발성 이벤트가 없습니다."
            )
            return
        if not self._audio_event_preflight():
            return
        self.analysis_mode = "ratio"
        self.analysis_busy = True
        self.ui.btn_run_analysis.setEnabled(False)
        job = ComputeJob(
            self.analysis_generation, run_assignment,
            tuple(self.audio_slots), tuple(self.events),
            None, None, None, None,
            self.ui.spin_n_fft.value(), self.ui.spin_hop_length.value()
        )
        job.signals.finished.connect(self._analysis_finished)
        self.pool.start(job)

    def _analysis_finished(self, request_generation, rows, error):
        """분석 모드별 결과창 분리 및 CSV 표 형식 표시."""
        self.analysis_busy = False
        self.ui.btn_run_analysis.setEnabled(True)
        if request_generation != self.analysis_generation:
            self.statusBar().showMessage("입력 변경으로 이전 분석 결과 폐기.", 5000)
            return
        if error:
            QMessageBox.critical(self, "Analysis", error)
            return
        mode = getattr(self, "analysis_mode", "ratio")
        self.results = rows
        self.analysis_results[mode] = rows
        target = {
            "ratio": self.ui.text_result,
            "distance": self.ui.text_dist_result,
            "geometry": self.ui.text_geo_result
        }[mode]
        target.setPlainText(self._results_csv_text(rows))
        self.export_button.setEnabled(bool(rows))
        n_assigned = sum(
            bool(row.channel_id) if mode == "ratio" else bool(row.mouse_id)
            for row in rows
        )
        self.statusBar().showMessage(
            f"{mode}: assigned {n_assigned}/{len(rows)}, "
            f"unassigned {len(rows) - n_assigned}. "
            "Detailed levels/margins are in the result panel.",
            12000
        )

    def _results_csv_text(self, results):
        """분석 결과를 CSV 행 형식으로 표시. subject는 채널 기반 후보일 수 있음."""
        import csv
        from io import StringIO
        buffer = StringIO()
        # Ratio-only 불확실성 진단에 필요한 수치를 GUI에도 노출한다.
        # 3dB 미만 기각인지 입력/라벨 시간 문제로 NaN인지 즉시 구분 가능.
        names = [
            "onset_s", "offset_s", "subject_id", "channel_id",
            "margin_db", "ch1_level_db", "ch2_level_db", "method", "reason"
        ]
        writer = csv.DictWriter(buffer, fieldnames=names)
        writer.writeheader()
        subjects = self.channel_subjects()
        for result in results:
            # 보정 모드가 'unassigned'라고 기각한 결과를 이전 ratio 채널 ID로
            # 보충하면 잘못된 assignment가 생긴다. baseline 결과만 채널 mapping 사용.
            if result.method == "ratio-only":
                candidate = subjects.get(result.channel_id, "unassigned")
            else:
                candidate = result.mouse_id or "unassigned"
            writer.writerow({
                "onset_s": f"{result.event.onset_s:.5f}",
                "offset_s": f"{result.event.offset_s:.5f}",
                "subject_id": candidate,
                "channel_id": result.channel_id or "",
                "margin_db": (f"{result.margin_db:.2f}"
                              if np.isfinite(result.margin_db) else "NaN"),
                "ch1_level_db": (
                    f"{result.levels_db['ch1']:.2f}"
                    if np.isfinite(result.levels_db.get("ch1", np.nan))
                    else "NaN"
                ),
                "ch2_level_db": (
                    f"{result.levels_db['ch2']:.2f}"
                    if np.isfinite(result.levels_db.get("ch2", np.nan))
                    else "NaN"
                ),
                "method": result.method, "reason": result.reason
            })
        return buffer.getvalue()

    def export_results(self):
        if not any(self.analysis_results.values()):
            QMessageBox.information(self, "CSV export", "먼저 분석을 실행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save assignment CSV", "assignment_results.csv", "CSV (*.csv)"
        )
        if path:
            try:
                # Ratio, distance, geometry의 모든 결과를 한 파일에 함께 저장.
                export_results_csv(path, self.analysis_results,
                                   channel_subjects=self.channel_subjects())
                self.statusBar().showMessage(f"Saved: {path}", 7000)
            except Exception as error:
                QMessageBox.critical(self, "CSV export", str(error))

    # ------------------------------------------------------------
    # Independent Audio preview / Video playback
    # ------------------------------------------------------------
    def _on_audio_channel_selected(self, _index):
        """마이크를 바꾸면 이전 채널의 재생 사본은 폐기한다."""
        self._stop_audio_preview()

    def _on_audio_timeline_seek(self, position_ms):
        """User seeks ORIGINAL seconds; linked mode also seeks VIDEO time."""
        if self._audio_preview_active:
            # Manual drag changes the next audio starting second.
            self._stop_audio_preview()
        self.draw_audio(position_ms)
        if self._play_all_active and self._sync_is_ready():
            video_s = self.sync.audio_to_video(float(position_ms) / 1000.0)
            if self.video_duration_ms > 0:
                video_ms = max(0, min(
                    self.video_duration_ms, round(video_s * 1000)
                ))
                self.player.setPosition(video_ms)

    def _stop_audio_preview(self):
        """Audio-only 재생을 정지하고 영상/원본 WAV는 건드리지 않는다."""
        self._audio_preview_active = False
        if hasattr(self, "audio_preview_player"):
            self.audio_preview_player.stop()
            self.audio_preview_player.setSource(QUrl())
        if hasattr(self, "btn_audio_play"):
            self.btn_audio_play.setText("▶")
        # Keep current playhead visible on the audio plot after Stop/Pause.
        if hasattr(self, "spectrogram_views"):
            for _, _, cursor in self.spectrogram_views:
                cursor.setVisible(
                    self.ui.slider_timeline_audio.maximum() > 0
                )
                cursor.setValue(
                    self.ui.slider_timeline_audio.value() / 1000.0
                )

    def _prepare_audio_preview(self, audio, start_s):
        """정확한 1x 재생: 원본 4초 -> 출력 WAV도 4초, 시간축 불변.

        기존 코드는 250kHz 원본 샘플을 그대로 48kHz WAV에 넣어 실제
        1초를 5.208초로 늘리는 결함이 있었다. 이제 scipy.resample_poly로
        샘플 개수를 변환하고 출력 sample rate를 48kHz로 고정한다.
        초음파(sr>=96k)는 60kHz 부근의 heterodyne으로 가청 대역 이동:
        이는 주파수 이동일 뿐 재생속도를 바꾸지 않는다. 원본 분석은 불변.
        """
        source_sr = int(audio.sr)
        if source_sr <= 0 or len(audio.samples) == 0:
            raise ValueError("Audio has invalid sample rate or is empty.")
        source_start = max(0, min(
            int(round(float(start_s) * source_sr)), len(audio.samples) - 1
        ))
        source_end = min(len(audio.samples), source_start + source_sr * 4)
        source = np.nan_to_num(
            np.asarray(audio.slice_float(source_start, source_end), np.float32),
            nan=0.0, posinf=0.0, neginf=0.0
        )
        if not source.size:
            raise ValueError("재생할 Audio 구간이 비어 있습니다.")

        preview_sr = 48000
        if source_sr >= 96000:
            # 250kHz 녹음의 약 60kHz USV를 beat frequency로 이동시킨다.
            # ADC의 Nyquist 한계에 맞춰 carrier는 자동으로 제한된다.
            carrier_hz = min(60000.0, source_sr * 0.38)
            t_local = np.arange(source.size, dtype=np.float64) / source_sr
            source = (
                source * (2.0 * np.cos(2.0 * np.pi * carrier_hz * t_local))
            ).astype(np.float32)

        common = gcd(source_sr, preview_sr)
        audible = resample_poly(
            source, preview_sr // common, source_sr // common
        ).astype(np.float32, copy=False)
        # PCM의 길이를 원본 sample count / sample rate에 맞춘다.
        expected = round(source.size * preview_sr / source_sr)
        if len(audible) < expected:
            audible = np.pad(audible, (0, expected-len(audible)))
        audible = audible[:expected]
        peak = float(np.max(np.abs(audible))) if audible.size else 0.0
        if peak > 1e-10:
            audible *= min(1.0, 0.75 / peak)
        samples_pcm16 = (np.clip(audible, -1.0, 1.0) * 32767).astype("<i2")

        self._audio_preview_file_counter += 1
        destination = (Path(self._audio_preview_tmpdir.name) /
                       f"usvoc_preview_{self._audio_preview_file_counter:04d}.wav")
        with wave.open(str(destination), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(preview_sr)
            out.writeframes(samples_pcm16.tobytes())

        start_actual_s = source_start / source_sr
        end_actual_s = source_end / source_sr
        # Backend QMediaPlayer.position() is now REAL elapsed milliseconds.
        return destination, start_actual_s, end_actual_s, 1.0

    def _start_audio_preview_at(self, start_s):
        """현재 채널의 원본 시간 start_s부터 독립 오디오 재생을 시작."""
        channel_idx = self.audio_channel_select.currentData()
        if channel_idx is None:
            raise ValueError("마이크 채널을 선택하세요.")
        channel_idx = int(channel_idx)
        if channel_idx >= len(self.audio_slots):
            raise ValueError("존재하지 않는 마이크 채널입니다.")
        audio = self.audio_slots[channel_idx]
        if audio is None:
            raise ValueError(f"Ch {channel_idx+1}에 WAV를 먼저 불러오세요.")

        path, start_actual, end_actual, rate = self._prepare_audio_preview(
            audio, start_s
        )
        # Stop and clear the previous source before assigning a new temp file.
        self._audio_preview_active = False
        self.audio_preview_player.stop()
        self.audio_preview_player.setSource(QUrl())
        previous_file = self._audio_preview_current_path
        self._audio_preview_current_path = path
        if previous_file is not None and previous_file != path:
            try:
                previous_file.unlink(missing_ok=True)
            except OSError:
                # Windows media backends may briefly hold the previous file open.
                # TemporaryDirectory cleanup will retry when the app closes.
                pass
        self._audio_preview_channel_idx = channel_idx
        self._audio_preview_start_s = start_actual
        self._audio_preview_end_s = end_actual
        self._audio_preview_time_scale = rate  # 1.0 after real-time resampling
        self._audio_preview_cursor_s = start_actual
        self._audio_preview_active = True
        self.audio_preview_player.setPlaybackRate(1.0)
        self.audio_preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_preview_player.play()
        self.btn_audio_play.setText("❚❚")
        self.btn_audio_play.setToolTip(
            "Audio playback: original 1 second = actual 1 second. "
            "Ultrasound may be shifted to audible frequencies, not slowed."
        )

    def toggle_audio_preview(self):
        """Audio 재생/일시정지. Video와 공통 재생 상태는 변경하지 않는다."""
        if self.audio_preview_player.playbackState() == (
            QMediaPlayer.PlaybackState.PlayingState
        ):
            self.audio_preview_player.pause()
            self.btn_audio_play.setText("▶")
            return
        if (self._audio_preview_active and
            self.audio_preview_player.playbackState() ==
                QMediaPlayer.PlaybackState.PausedState):
            self.audio_preview_player.play()
            self.btn_audio_play.setText("❚❚")
            return

        # 미리듣기 채널이 비어 있고 다른 채널이 로드되어 있다면 첫 로드 채널 선택.
        chosen = self.audio_channel_select.currentData()
        if chosen is not None and (
            int(chosen) >= len(self.audio_slots) or
            self.audio_slots[int(chosen)] is None
        ):
            available = next(
                (i for i, item in enumerate(self.audio_slots) if item is not None),
                None
            )
            if available is not None:
                self.audio_channel_select.setCurrentIndex(available)

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self._play_all_active = False
        self._update_video_play_buttons()
        try:
            current_ms = self.ui.slider_timeline_audio.value()
            total_ms = self.ui.slider_timeline_audio.maximum()
            if total_ms > 0 and current_ms >= total_ms - 10:
                # Pressing Play after EndOfMedia starts from the beginning.
                current_ms = 0
                with QSignalBlocker(self.ui.slider_timeline_audio):
                    self.ui.slider_timeline_audio.setValue(0)
                self.draw_audio(0)
            self._start_audio_preview_at(current_ms / 1000.0)
        except Exception as error:
            self._stop_audio_preview()
            QMessageBox.warning(self, "Audio preview", str(error))

    def _on_audio_preview_position(self, preview_ms):
        """Qt playback clock (real milliseconds) -> original WAV playhead.

        The temporary WAV is resampled to 48 kHz with its DURATION preserved;
        one second from QMediaPlayer now corresponds to one second of source.
        """
        if not self._audio_preview_active:
            return
        audio = self.audio_slots[self._audio_preview_channel_idx]
        if audio is None:
            return
        original_s = min(
            self._audio_preview_end_s,
            self._audio_preview_start_s +
            max(0, float(preview_ms)) / 1000.0
        )
        self._audio_preview_cursor_s = original_s
        head_ms = max(0, min(
            self.ui.slider_timeline_audio.maximum(), round(original_s * 1000)
        ))

        # Qt signal-blocking prevents a programmatic playhead update from
        # being mistaken for a human drag/seek. Update EVERY tick, not only
        # when the 3-second plot window scrolls.
        if not self.ui.slider_timeline_audio.isSliderDown():
            with QSignalBlocker(self.ui.slider_timeline_audio):
                self.ui.slider_timeline_audio.setValue(head_ms)
        self.draw_audio(head_ms)  # only recalculates STFT if window changed

    def _on_audio_preview_media_status(self, status):
        """최대 4초 단위로 원본의 다음 구간을 생성해 연속 미리듣기."""
        if (status != QMediaPlayer.MediaStatus.EndOfMedia or
                not self._audio_preview_active):
            return
        audio = self.audio_slots[self._audio_preview_channel_idx]
        if audio is None:
            self._stop_audio_preview()
            return
        next_s = self._audio_preview_end_s
        if next_s >= audio.duration_s - 1e-5:
            self._stop_audio_preview()
            return

        # EndOfMedia 내부에서 즉시 player source를 바꾸면 backend가 꼬일 수 있어
        # 한 번 Qt 이벤트 루프로 돌아간 다음 재생을 이어간다.
        QTimer.singleShot(0, lambda: self._continue_audio_preview(next_s))

    def _continue_audio_preview(self, next_s):
        if not self._audio_preview_active:
            return
        try:
            self._start_audio_preview_at(next_s)
        except Exception as error:
            self._stop_audio_preview()
            self.statusBar().showMessage(f"Audio preview: {error}", 6000)

    def _on_audio_preview_error(self, error, message):
        """Qt가 생성한 PCM16 미리듣기 WAV를 열지 못한 경우 명확히 알린다."""
        if error == QMediaPlayer.Error.NoError or not self._audio_preview_active:
            return
        self._stop_audio_preview()
        self.statusBar().showMessage(
            f"Audio playback error: {message}", 10000
        )

    def closeEvent(self, event):
        """재생 종료 시 Qt 미디어 핸들과 임시 가청 미리듣기 파일을 정리한다."""
        self._audio_preview_active = False
        self.audio_preview_player.stop()
        self.audio_preview_player.setSource(QUrl())
        self.player.stop()
        temp = self._audio_preview_tmpdir
        self._audio_preview_tmpdir = None
        if temp is not None:
            try:
                temp.cleanup()
            except OSError:
                # Windows occasionally releases decoder handles after Qt teardown.
                # A stale temp preview should never prevent closing the UI.
                pass
        super().closeEvent(event)

    def toggle_video_only(self):
        """Video 탭 버튼은 영상만 재생. 다른 탭의 WAV 시각은 이동하지 않는다."""
        if not self.has_video:
            QMessageBox.information(self, "Video playback", "영상 파일을 먼저 불러오세요.")
            return
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if self._play_all_active:
                # 공통 재생 중에는 일시정지보다 영상 단독 모드로 전환.
                self._play_all_active = False
            else:
                self.player.pause()
            self._update_video_play_buttons()
            return

        self._stop_audio_preview()
        self._play_all_active = False
        if (self.video_duration_ms > 0
            and self.player.position() >= self.video_duration_ms - 10):
            self.player.setPosition(0)
        self.player.setPlaybackRate(1.0)
        self.player.play()
        self._update_video_play_buttons()

    def _update_video_play_buttons(self, _state=None):
        """현재 재생 모드에 해당하는 버튼에만 Pause 아이콘을 표시한다."""
        playing = self.player.playbackState() == (
            QMediaPlayer.PlaybackState.PlayingState
        )
        self.btn_video_play.setText(
            "❚❚" if playing and not self._play_all_active else "▶"
        )
        self.ui.btn_play_all.setText(
            "❚❚" if playing and self._play_all_active else "▶"
        )

    def toggle_video(self):
        """하단 Play All: 영상 시계에 따라 동기화된 Audio 그래프를 표시한다.

        Audio-only 버튼이 만든 '느린 가청용 사본'을 공통 재생에서 동시에 틀면
        재생 속도 차이로 Audio/Video 동기가 깨진다. 따라서 Play All은 영상을
        주 시계로 하고 원본 시각의 스펙트로그램과 커서를 함께 이동한다.
        독립 Audio 버튼으로는 느린 소리 미리듣기가 가능하다.
        """
        if not self.has_video:
            QMessageBox.information(self, "Playback", "먼저 영상 파일을 불러오세요.")
            return
        is_playing = (self.player.playbackState() ==
                      QMediaPlayer.PlaybackState.PlayingState)
        if is_playing and self._play_all_active:
            self.player.pause()
            self._play_all_active = False
            self._update_video_play_buttons()
            return

        self._stop_audio_preview()
        self._play_all_active = True
        start_ms, end_ms = self._common_playback_bounds_ms()
        if (
            self.video_duration_ms > 0
            and self.player.position() >= self.video_duration_ms - 10
        ):
            self.player.setPosition(start_ms)
        elif (self._sync_is_ready() and
              (self.player.position() < start_ms or self.player.position() >= end_ms)):
            self.player.setPosition(start_ms)
        self.player.setPlaybackRate(1.0)
        if not is_playing:
            self.player.play()
        else:
            # Video-only 재생 중이던 영상은 멈추지 않고 공통 모드로 전환.
            self.video_position(self.player.position())
        self._update_video_play_buttons()

    def _common_playback_bounds_ms(self):
        """동일 동기화 사건부터 두 매체 모두 데이터가 존재하는 영상 시각 범위.

        offset_s는 t_audio=t_video+offset_s. 공통 종료는 영상 종료와
        최단 오디오 채널 종료 중 더 빠른 순간이며, 타임라인 구간은 [start,end].
        """
        if not self._sync_is_ready() or not any(self.audio_slots):
            return 0, self.video_duration_ms
        start_ms = max(0, int(round(self.video_start_s * 1000)))
        shortest_audio_s = min(a.duration_s for a in self.audio_slots if a is not None)
        audio_end_video_s = self.sync.audio_to_video(shortest_audio_s)
        end_ms = min(self.video_duration_ms, int(round(audio_end_video_s * 1000)))
        return start_ms, max(start_ms, end_ms)

    def _seek_common_timeline(self, elapsed_ms):
        """Bottom slider uses elapsed milliseconds AFTER chosen sync starts."""
        start_ms, end_ms = self._common_playback_bounds_ms()
        self.player.setPosition(min(end_ms, start_ms + max(0, int(elapsed_ms))))

    def _poll_playback_positions(self):
        """Refresh moving slider thumbs from the real Qt media clocks at 20 Hz.

        QMediaPlayer.positionChanged callbacks can be sparse on some Windows
        backends. Polling QMediaPlayer.position() adds visual fluidity without
        advancing an invented clock or desynchronizing seconds.
        """
        if self.has_video:
            duration = int(self.player.duration())
            if duration > 0 and duration != self.video_duration_ms:
                self._on_video_duration(duration)
            if self.player.playbackState() == (
                QMediaPlayer.PlaybackState.PlayingState
            ):
                self.video_position(self.player.position())
        if (
            self._audio_preview_active
            and self.audio_preview_player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._on_audio_preview_position(
                self.audio_preview_player.position()
            )

    @staticmethod
    def _format_ms(value_ms):
        seconds = max(0, int(value_ms) // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _on_video_playback_error(self, error, message):
        """Expose Qt codec/metadata problems instead of a stationary slider."""
        if error != QMediaPlayer.Error.NoError:
            self.statusBar().showMessage(
                f"Video playback error: {message}", 12000
            )

    def _on_video_duration(self, duration_ms):
        """Video/Play All slider ranges use milliseconds and the real media duration."""
        self.video_duration_ms = max(0, int(duration_ms))
        position_ms = max(0, int(self.player.position()))
        with QSignalBlocker(self.ui.slider_timeline_video):
            self.ui.slider_timeline_video.setRange(0, self.video_duration_ms)
            self.ui.slider_timeline_video.setValue(position_ms)
        start_ms, end_ms = self._common_playback_bounds_ms()
        common_elapsed = max(0, min(position_ms-start_ms, end_ms-start_ms))
        with QSignalBlocker(self.ui.slider_timeline_all):
            self.ui.slider_timeline_all.setRange(0, max(0, end_ms-start_ms))
            self.ui.slider_timeline_all.setValue(common_elapsed)
        self.ui.lbl_time_video.setText(
            f"{self._format_ms(position_ms)} / "
            f"{self._format_ms(self.video_duration_ms)}"
        )
        self.ui.label_time_all.setText(
            f"{self._format_ms(common_elapsed)} / "
            f"{self._format_ms(end_ms-start_ms)}"
        )

    def video_position(self, position_ms):
        """재생 시각 업데이트: STFT 전체 재계산이 아닌 가벼운 cursor 이동."""
        if not self.ui.slider_timeline_video.isSliderDown():
            with QSignalBlocker(self.ui.slider_timeline_video):
                self.ui.slider_timeline_video.setValue(position_ms)
        start_ms, end_ms = self._common_playback_bounds_ms()
        common_elapsed = max(0, min(position_ms-start_ms, end_ms-start_ms))
        if not self.ui.slider_timeline_all.isSliderDown():
            with QSignalBlocker(self.ui.slider_timeline_all):
                self.ui.slider_timeline_all.setValue(common_elapsed)
        self.ui.lbl_time_video.setText(
            f"{self._format_ms(position_ms)} / {self._format_ms(self.video_duration_ms)}"
        )
        self.ui.label_time_all.setText(
            f"{self._format_ms(common_elapsed)} / "
            f"{self._format_ms(end_ms-start_ms)}"
        )
        # 영상 단독 재생은 전체 비디오 끝까지 허용한다. 양 매체 중 먼저
        # 종료하는 공통 구간 제한은 하단 Play All 모드에서만 적용.
        if (self._play_all_active and self._sync_is_ready()
            and end_ms > start_ms and position_ms >= end_ms
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self.player.pause()

        # Video-only 모드에서는 Audio/Video Sync 기준점이 있더라도
        # 독립 Audio 타임라인을 이동하지 않는다. 공통 재생일 때만 연동.
        if not (self._sync_is_ready() and self._play_all_active):
            return
        audio_ms = max(0, round(self.sync.video_to_audio(position_ms / 1000) * 1000))
        audio_duration_s = min(
            (a.duration_s for a in self.audio_slots if a is not None), default=0.0
        )
        self.ui.lbl_time_audio.setText(
            f"{self._format_ms(audio_ms)} / {self._format_ms(audio_duration_s*1000)}"
        )
        for _, _, playhead in self.spectrogram_views:
            playhead.setValue(audio_ms / 1000)
        # Audio slider must follow the synchronized PLAYHEAD every tick.
        # Block valueChanged to avoid treating automatic updates as user seeks.
        audio_ms = max(0, min(
            audio_ms, self.ui.slider_timeline_audio.maximum()
        ))
        if not self.ui.slider_timeline_audio.isSliderDown():
            with QSignalBlocker(self.ui.slider_timeline_audio):
                self.ui.slider_timeline_audio.setValue(audio_ms)
            self.draw_audio(audio_ms)  # STFT recalculated only at window boundary

    def run_test_analysis(self, mode):
        """Test 탭의 distance/geometry 모델. 원본 ratio-only 결과와 별도 저장.

        대상은 A/B 두 개체 실험이며 현재 geometry JSON, validation threshold,
        두 Sync marker, SLEAP 좌표, Anchor subject ID가 필수다.
        """
        if self.analysis_busy:
            return
        missing = []
        if len(self.audio_slots) != 2 or any(a is None for a in self.audio_slots):
            missing.append("two audio files")
        if (not self.events or len(self.label_paths) != len(self.audio_slots)
            or not all(self.label_paths)):
            missing.append("N channel-specific USV label files")
        if self.tracking_df is None:
            missing.append("SLEAP tracking CSV/.slp")
        if not self.anchor_mapping:
            missing.append("confirmed Audio ↔ Video subject/track association")
        if not self._sync_is_ready():
            missing.append("both Audio/Video Sync markers")
        if not self.geometry or not self.empirical_cfg or not self.empirical_threshold:
            missing.append("measured geometry + trained median/MAD JSON")
        if missing:
            QMessageBox.warning(self, "Experimental model", "Required: " + ", ".join(missing))
            return
        if not self._audio_event_preflight():
            return
        try:
            subjects = self.channel_subjects()
            # Require explicit Channel -> Subject -> SLEAP Track matching.
            # A matching set of IDs alone does NOT establish the association.
            validate_audio_video_mapping(self.anchor_mapping, subjects)
        except Exception as error:
            QMessageBox.warning(self, "Subject IDs", str(error))
            return

        self.analysis_busy = True
        self.analysis_mode = mode
        self.ui.btn_run_analysis.setEnabled(False)
        job = ComputeJob(
            self.analysis_generation, run_empirical_assignment,
            tuple(self.audio_slots), tuple(self.events), self.tracking_df,
            self.anchor_mapping, self.geometry, self.empirical_cfg,
            self.empirical_threshold, subjects, self.sync,
            mode == "distance",
            self.ui.spin_n_fft.value(), self.ui.spin_hop_length.value()
        )
        job.signals.finished.connect(self._analysis_finished)
        self.pool.start(job)

    def show_result_overlay(self, mode):
        """현재 보이는 전체 마이크 스펙트로그램에 onset/offset별 ID 주석 표시."""
        if not self.analysis_results.get(mode):
            QMessageBox.information(self, "Assignment labels", "먼저 해당 분석을 실행하세요.")
            return
        self.active_overlay_mode = mode
        self._refresh_result_overlay()
        self.ui.tab_content.setCurrentWidget(self.ui.tab_audio)

    def fit_training_threshold(self):
        """선택된 training 파일의 R_corr열로 R_mid/비보정 MAD 학습.

        이 training CSV는 현재 empirical geometry 설정(단위·가중치 모드)에
        맞춰 이미 계산된 R_corr값이어야 한다. Validation CSV와 절대 중복 금지.
        이 버튼은 검증 정확도를 측정하지 않고 임계값만 업데이트한다.
        """
        from voc_assign.ratio_corr import fit_empirical_threshold
        if self.empirical_cfg is None:
            QMessageBox.warning(self, "Training", "먼저 empirical 설정이 포함된 geometry.json을 불러오세요.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Training corrected ratios (R_corr)", "", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            review = CSVHeaderDialog(path, kind="training", parent=self)
            if review.exec() != QDialog.DialogCode.Accepted:
                return
            dataframe = read_delimited_table(path, review.result_spec)
            if "r_corr" not in dataframe.columns:
                raise ValueError("Training CSV must contain r_corr column (natural-log ratio).")
            values = pd.to_numeric(dataframe["r_corr"], errors="raise").to_numpy()
            learned = fit_empirical_threshold(values, k=self.empirical_cfg.k)
            self.empirical_threshold = learned
            self.training_path = str(path)
            self._invalidate_results()
            QMessageBox.information(
                self, "Training complete",
                f"R_mid={learned.r_mid:.5f}, MAD={learned.mad:.5f}, "
                f"k={learned.k:g}.\nKeep the validation dataset separate."
            )
        except Exception as error:
            QMessageBox.critical(self, "Training error", str(error))

    def validate_results(self):
        """학습에 사용하지 않은 정답 CSV로 ratio/distance/geometry를 정량 비교.

        정답 형식: onset_s,offset_s,mouse_id. 각 결과 USV onset/offset과
        5ms 이내에 일치하는 정답 행에 한하여 계산하고 매칭 수를 보고한다.
        기준 데이터를 임계값 학습에 재사용하면 과대평가되므로 금지.
        """
        from voc_assign.ratio_corr import validate_empirical_predictions
        if not any(self.analysis_results.values()):
            QMessageBox.information(self, "Validation", "분석 결과를 먼저 생성하세요.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Ground-truth validation data (holdout)", "", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            if self.training_path and Path(path).resolve() == Path(self.training_path).resolve():
                raise ValueError("Training and validation data cannot be the same file.")
            review = CSVHeaderDialog(path, kind="validation", parent=self)
            if review.exec() != QDialog.DialogCode.Accepted:
                return
            ground = read_delimited_table(path, review.result_spec)
            required = {"onset_s", "offset_s", "mouse_id"}
            if not required.issubset(ground.columns):
                raise ValueError("Ground truth must contain onset_s, offset_s, mouse_id.")
            ground["onset_s"] = pd.to_numeric(ground["onset_s"], errors="raise")
            ground["offset_s"] = pd.to_numeric(ground["offset_s"], errors="raise")
            ground["mouse_id"] = ground["mouse_id"].astype(str)
            report = {}
            for mode, results in self.analysis_results.items():
                predicted, truth = [], []
                matched_rows = set()
                for result in results:
                    diff = (ground.onset_s - result.event.onset_s).abs() + (
                        ground.offset_s - result.event.offset_s
                    ).abs()
                    if diff.empty:
                        continue
                    row = int(diff.idxmin())
                    if diff.loc[row] > 0.010 or row in matched_rows:
                        continue
                    matched_rows.add(row)
                    # 기하학적 기각 결과는 반드시 미할당(None)으로 평가.
                    # 그렇지 않으면 ratio-only 예측을 대신 사용해 보정 정확도를
                    # 부풀리게 된다.
                    if result.method == "ratio-only":
                        guess = self.channel_subjects().get(result.channel_id)
                    else:
                        guess = result.mouse_id
                    predicted.append(guess)
                    truth.append(ground.loc[row, "mouse_id"])
                if truth:
                    metrics = validate_empirical_predictions(predicted, truth)
                    metrics["matched"] = len(truth)
                    report[mode] = metrics
            if not report:
                raise ValueError("No results match ground truth to within ±5 ms per boundary.")
            self.validation_metrics = report
            descriptions = [
                f"{mode}: N={m['matched']}, coverage={m['coverage']:.1%}, "
                f"accuracy(assigned)={m['accuracy_assigned']:.1%}, "
                f"accuracy(all)={m['accuracy_all']:.1%}"
                for mode, m in report.items()
            ]
            self.validation_result.setText("\n".join(descriptions))
            QMessageBox.information(self, "Validation results", "\n".join(descriptions))
        except Exception as error:
            QMessageBox.critical(self, "Validation error", str(error))

    def _refresh_result_overlay(self):
        """STFT를 재계산하지 않고 기존 그래프의 TextItem만 변경."""
        for plot, item in self.overlay_items:
            try:
                plot.removeItem(item)
            except RuntimeError:
                pass  # 새 그래프가 이전 PlotWidget을 이미 파괴한 경우.
        self.overlay_items = []
        results = self.analysis_results.get(getattr(self, "active_overlay_mode", ""), [])
        if not results:
            return
        starts = self.window_start_ms / 1000
        ends = starts + self.VIEW_SECONDS
        subjects = self.channel_subjects()
        for result in results:
            event = result.event
            if event.offset_s < starts or event.onset_s > ends:
                continue
            # Geometry/Distance가 'unassigned'라고 판정한 경우 baseline 채널명으로
            # 대체하지 않는다. 그래프의 표기와 결과 CSV가 일치해야 한다.
            if result.method == "ratio-only":
                text_id = subjects.get(result.channel_id, "unassigned")
            else:
                text_id = result.mouse_id or "unassigned"
            for plot, _, _ in self.spectrogram_views:
                # fmax 위 7px 위치에 표기. Hz/pixel은 확대 비율/창 크기마다 다르므로
                # 현재 ViewBox 높이를 기준으로 매번 계산해 5~10px 요구를 유지.
                # Frequency column이 없다면 화면 상단에서 약 12px 아래에 배치.
                low_y, high_y = plot.viewRange()[1]
                vb_height_px = max(float(plot.plotItem.vb.height()), 1.0)
                hz_per_px = max((high_y - low_y) / vb_height_px, 1e-9)
                if getattr(event, "fmax_hz", None) is not None:
                    label_y = min(float(event.fmax_hz) + 7 * hz_per_px,
                                  high_y - 5 * hz_per_px)
                    label_y = max(low_y + 5 * hz_per_px, label_y)
                else:
                    label_y = high_y - 12 * hz_per_px
                item = pg.TextItem(text=str(text_id), color="#FFFADC",
                                   anchor=(0, 1), fill=pg.mkBrush(18, 30, 39, 225))
                item.setPos(max(starts, event.onset_s), label_y)
                item.setZValue(100)
                plot.addItem(item)
                self.overlay_items.append((plot, item))

    def save_pdf(self):
        """현재 화면에 plot된 모든 N채널 스펙트로그램+분석 표를 한 PDF로 저장.

        '현재까지 plot된 이미지'는 현재 보이는 3초 구간의 각 채널 그래프를 뜻한다.
        5분 전체 STFT를 고해상도로 한 번에 PDF로 저장하면 메모리가 매우
        커질 수 있으므로 전체 타임라인 출력은 별도 배치 기능으로 구분한다.
        """
        if not self.spectrogram_views or not any(self.audio_slots):
            QMessageBox.information(self, "PDF", "먼저 WAV와 스펙트로그램을 로드하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export plots and assignments", "USVocAssigner_report.pdf",
            "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            pdf = QPdfWriter(path)
            pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            pdf.setResolution(150)
            painter = QPainter(pdf)
            if not painter.isActive():
                raise RuntimeError("Unable to initialize PDF writer.")
            try:
                page_w, page_h = pdf.width(), pdf.height()
                left, top, bottom = 85, 85, 75
                y = top
                font = QFont("Malgun Gothic", 11)
                painter.setFont(font)
                painter.setPen(QColor("#121B24"))
                painter.drawText(left, y, "USVocAssigner — spectrogram & assignment report")
                y += 55
                painter.drawText(left, y, "Current window: "
                    + f"{self.window_start_ms/1000:.3f}s, "
                    + f"scale={self.spectrogram_levels_db} dB rel.")
                y += 35

                def next_page_if_needed(height):
                    nonlocal y
                    if y + height > page_h - bottom:
                        pdf.newPage()
                        y = top

                for index, (plot, _, _) in enumerate(self.spectrogram_views):
                    if self.audio_slots[index] is None:
                        continue
                    image = plot.grab().toImage()  # GUI thread: capture this channel's plot
                    if image.isNull():
                        continue
                    width = page_w - 2*left
                    height = int(width*image.height()/max(image.width(), 1))
                    next_page_if_needed(height + 40)
                    painter.drawText(left, y, f"ch{index+1}: "
                                     + self.subject_fields[index].text().strip())
                    y += 20
                    painter.drawImage(QRectF(left, y, width, height), image)
                    y += height + 35

                for mode, result in self.analysis_results.items():
                    if not result:
                        continue
                    next_page_if_needed(70)
                    painter.drawText(left, y, f"{mode.upper()} — {len(result)} events")
                    y += 32
                    for line in self._results_csv_text(result).splitlines():
                        next_page_if_needed(24)
                        # 긴 reason 필드는 한 줄에서 잘라 PDF 바깥으로 넘치지 않게 함.
                        painter.drawText(left, y, line[:125])
                        y += 24
            finally:
                painter.end()
            self.statusBar().showMessage(f"Report saved: {path}", 7000)
        except Exception as error:
            QMessageBox.critical(self, "PDF export", str(error))

    def save_process(self):
        """세션을 .usvoc.json으로 저장 (오디오/영상 원본은 별도 파일로 보존).

        포함: 각 채널 경로·subject_id·sample rate, label/video/tracking,
        anchor, geometry 설정 경로, Sync 두 marker, STFT/색상 범위,
        세 분석 결과와 표시 시간. 절대 경로를 사용하므로 파일 이동 시 안내.
        """
        from dataclasses import asdict
        path, _ = QFileDialog.getSaveFileName(
            self, "Save session", "USVocAssigner.usvoc.json",
            "USVocAssigner session (*.usvoc.json);;JSON (*.json)"
        )
        if not path:
            return
        payload = {
            "format": "USVocAssigner-session", "version": 1,
            "audio": [
                {"path": str(audio.path) if audio is not None else None,
                 "sr": audio.sr if audio is not None else None,
                 "csv_spec": self.audio_csv_specs[i],
                 "subject_id": self.subject_fields[i].text().strip()}
                for i, audio in enumerate(self.audio_slots)
            ],
            "video": self.ui.line_video_path.text().strip() or None,
            # Preserve a separate reviewed CSV-header mapping for EACH mic.
            "label": self.label_path,  # Legacy Ch1 alias; old session compatibility.
            "labels": [
                {"path": path, "spec": self.label_specs[i]}
                for i, path in enumerate(self.label_paths)
            ],
            "tracking": self.tracking_path,
            "tracking_csv_spec": self.tracking_csv_spec,
            "geometry": getattr(self, "geometry_path", None),
            # Fit median/MAD from training R_corr may override threshold in geometry.json.
            "training_path": self.training_path,
            "empirical_threshold": (
                asdict(self.empirical_threshold)
                if self.empirical_threshold is not None else None
            ),
            "anchor_mapping": self.anchor_mapping,
            "audio_start_s": self.audio_start_s,
            "video_start_s": self.video_start_s,
            "n_fft": self.ui.spin_n_fft.value(),
            "hop_length": self.ui.spin_hop_length.value(),
            "view_start_ms": self.window_start_ms,
            "color_levels_db": list(self.spectrogram_levels_db),
            "results": {
                mode: [asdict(x) for x in rows]
                for mode, rows in self.analysis_results.items()
            },
        }
        try:
            Path(path).write_text(
                json.dumps(_json_safe(payload), indent=2,
                           ensure_ascii=False, allow_nan=False), encoding="utf-8"
            )
            self.statusBar().showMessage(f"Session saved: {path}", 6000)
        except Exception as error:
            QMessageBox.critical(self, "Session save", str(error))

    def open_process(self):
        """이전에 저장한 파일 경로, Sync anchor, 결과를 다시 로드.

        원본 WAV/영상 데이터를 세션 JSON 내부에 복제하지는 않는다.
        파일을 옮긴 경우 경로를 되돌리거나 각 행에서 다시 선택해야 한다.
        """
        from audio.data_load import USVEvent
        from voc_assign.voc_assign import AssignmentResult
        from video.track_run import load_tracking_table

        path, _ = QFileDialog.getOpenFileName(
            self, "Open session", "",
            "USVocAssigner session (*.usvoc.json *.json);;All (*.*)"
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("format") != "USVocAssigner-session" or payload.get("version") != 1:
                raise ValueError("Unsupported session file/version.")
            audio_rows = payload.get("audio", [])
            if len(audio_rows) < 2 or len(audio_rows) > 16:
                raise ValueError("Session needs 2~16 audio channels.")
            # 파일이 없으면 state를 일부만 바꾼 상태가 되지 않도록 먼저 검증.
            label_rows_saved = payload.get("labels")
            if label_rows_saved is None:
                # Old v1 JSON has only a single global label file.
                # Use it as Ch1; user must select Ch2..ChN before new analysis.
                label_rows_saved = [{"path": payload.get("label"), "spec": None}]
            if len(label_rows_saved) > len(audio_rows):
                raise ValueError("Session label count exceeds microphone count.")
            label_rows_saved = list(label_rows_saved) + [
                {"path": None, "spec": None}
                for _ in range(len(audio_rows)-len(label_rows_saved))
            ]
            paths = [row.get("path") for row in audio_rows]
            label_files = [row.get("path") for row in label_rows_saved]
            other_paths = [
                payload.get(key) for key in ("video", "tracking", "geometry")
            ]
            for source in paths + label_files + other_paths:
                if source and not Path(source).is_file():
                    raise FileNotFoundError(f"Referenced file not found: {source}")
            # Legacy sessions may not contain reviewed CSV-header mappings.
            # Require user confirmation instead of silently guessing columns.
            for i, row in enumerate(audio_rows):
                filename = row.get("path")
                if (filename and Path(filename).suffix.lower() == ".csv"
                    and row.get("csv_spec") is None):
                    dlg = CSVHeaderDialog(filename, kind="waveform", parent=self)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        return
                    row["csv_spec"] = dlg.result_spec
            candidate = [
                load_audio(
                    row["path"], f"ch{i+1}", csv_sr=row.get("sr"),
                    csv_spec=row.get("csv_spec")
                )
                if row.get("path") else None
                for i, row in enumerate(audio_rows)
            ]
            self._validate_slots(candidate)
            saved_hop = int(payload.get("hop_length", 512))
            candidate_labels = []
            for i, entry in enumerate(label_rows_saved):
                if entry.get("path") and entry.get("spec") is None:
                    audio = candidate[i]
                    dlg = CSVHeaderDialog(
                        entry["path"], kind="labels",
                        sampling_rate=audio.sr if audio is not None else None,
                        hop_length=saved_hop, parent=self
                    )
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        return
                    entry["spec"] = dlg.result_spec
                if not entry.get("path"):
                    candidate_labels.append([])
                    continue
                audio = candidate[i]
                candidate_labels.append(load_labels(
                    entry["path"], table_spec=entry.get("spec"),
                    sr=audio.sr if audio is not None else None,
                    hop_length=saved_hop
                ))

            while len(self.audio_rows) < len(audio_rows):
                self.add_audio_channel()
            while len(self.audio_rows) > len(audio_rows):
                self.remove_audio_channel(len(self.audio_rows)-1)

            self.audio_slots = candidate
            self.audio_csv_specs = [
                row.get("csv_spec") for row in audio_rows
            ]
            self.label_paths = [row.get("path") for row in label_rows_saved]
            self.label_specs = [row.get("spec") for row in label_rows_saved]
            self.label_events = candidate_labels
            self._reflow_label_grid()
            for index, row in enumerate(audio_rows):
                self.subject_fields[index].setText(row.get("subject_id", f"mouse_{index+1}"))
            self._apply_audio_update()

            self.ui.spin_n_fft.setValue(int(payload.get("n_fft", 2048)))
            self.ui.spin_hop_length.setValue(int(payload.get("hop_length", 512)))
            levels = payload.get("color_levels_db", [-100, 0])
            if len(levels) == 2 and float(levels[0]) < float(levels[1]):
                self.color_min_spin.setValue(float(levels[0]))
                self.color_max_spin.setValue(float(levels[1]))

            video = payload.get("video")
            if video:
                self.ui.line_video_path.setText(video)
                self.player.setSource(QUrl.fromLocalFile(video))
                self.has_video = True
                self.ui.btn_play_all.setEnabled(True)
                self.btn_video_play.setEnabled(True)
                self._update_video_play_buttons()

            # Label state was restored per channel before _apply_audio_update.
            self._recompute_label_events()
            self.tracking_path = payload.get("tracking")
            self.tracking_csv_spec = payload.get("tracking_csv_spec")
            if self.tracking_path:
                import cv2
                capture = cv2.VideoCapture(video)
                fps = capture.get(cv2.CAP_PROP_FPS)
                capture.release()
                if Path(self.tracking_path).suffix.lower() == ".csv":
                    if self.tracking_csv_spec is None:
                        dlg = CSVHeaderDialog(
                            self.tracking_path, kind="tracking", parent=self
                        )
                        if dlg.exec() != QDialog.DialogCode.Accepted:
                            raise ValueError("Tracking CSV header review was canceled.")
                        self.tracking_csv_spec = dlg.result_spec
                    raw_track = read_delimited_table(
                        self.tracking_path, self.tracking_csv_spec
                    )
                else:
                    raw_track = load_tracking_table(self.tracking_path)
                self.tracking_df = process_tracking_data(raw_track, fps)
                self.ui.line_tracking_file.setText(self.tracking_path)
            else:
                self.tracking_df = None
                self.ui.line_tracking_file.clear()

            self.anchor_mapping = payload.get("anchor_mapping")
            # Legacy session mapping (track_to_mouse only) must be re-confirmed.
            # Display invalid state; empirical analysis will reject it.
            self._update_audio_video_mapping_status()
            self.geometry_path = payload.get("geometry")
            self.training_path = payload.get("training_path")
            self.geometry = None
            self.empirical_cfg = None
            self.empirical_threshold = None
            if self.geometry_path:
                self.geometry = load_geometry_json(self.geometry_path)
                settings = json.loads(Path(self.geometry_path).read_text(encoding="utf-8"))
                if "empirical" in settings:
                    self.empirical_cfg = EmpiricalGeoConfig(**settings["empirical"])
                if "threshold" in settings:
                    self.empirical_threshold = EmpiricalThreshold(**settings["threshold"])
                self.label_geometry.setText(f"Geometry: {Path(self.geometry_path).name}")

            # 저장 당시 training CSV로 새로 학습한 경계가 있다면 복원한다.
            learned = payload.get("empirical_threshold")
            if learned is not None:
                self.empirical_threshold = EmpiricalThreshold(**learned)
            self.audio_start_s = payload.get("audio_start_s")
            self.video_start_s = payload.get("video_start_s")
            self._recalculate_sync_from_anchors()
            self.analysis_results = {"ratio": [], "distance": [], "geometry": []}
            def restore_result(record):
                # JSON null은 원래 계산 불가능했던 float(NaN) 값을 나타낸다.
                rec = dict(record)
                rec["event"] = USVEvent(**rec["event"])
                if rec.get("margin_db") is None:
                    rec["margin_db"] = float("nan")
                for key in ("levels_db", "candidate_errors_db"):
                    rec[key] = {
                        label: float("nan") if val is None else val
                        for label, val in (rec.get(key) or {}).items()
                    }
                return AssignmentResult(**rec)

            for mode, records in payload.get("results", {}).items():
                if mode not in self.analysis_results:
                    continue
                self.analysis_results[mode] = [restore_result(rec) for rec in records]
            self.ui.text_result.setPlainText(
                self._results_csv_text(self.analysis_results["ratio"])
                if self.analysis_results["ratio"] else ""
            )
            self.ui.text_dist_result.setPlainText(
                self._results_csv_text(self.analysis_results["distance"])
                if self.analysis_results["distance"] else ""
            )
            self.ui.text_geo_result.setPlainText(
                self._results_csv_text(self.analysis_results["geometry"])
                if self.analysis_results["geometry"] else ""
            )
            self.results = (self.analysis_results["geometry"]
                            or self.analysis_results["distance"]
                            or self.analysis_results["ratio"])
            self.export_button.setEnabled(bool(self.results))
            position = max(0, min(
                int(payload.get("view_start_ms", 0)),
                self.ui.slider_timeline_audio.maximum()
            ))
            self.ui.slider_timeline_audio.setValue(position)
            self.draw_audio(position, force=True)
            self.statusBar().showMessage(f"Session loaded: {path}", 7000)
        except Exception as error:
            QMessageBox.critical(self, "Session load", str(error))

    # 기존 Sync 탐색 함수는 mark_audio_start / mark_video_start로 대체했다.
    # 두 기준점을 고정한 이후에는 공통 Play와 영상 타임라인 이동이
    # 모든 오디오 스펙트로그램의 재생 커서를 같은 동기 시간으로 이동시킨다.

# 남은 연구 과제: USV 자체의 가청화, 하드웨어 동기 사건 추출, 개체 교차 ID 검증,
# 실측 거리/각도 파라미터 학습 및 holdout validation, 배포 환경 codecs/driver 테스트.
