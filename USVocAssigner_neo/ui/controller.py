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
# 제한: 오디오의 실제 초음파 가청화/오디오 파일 재생은 미구현.
# 제한: 특정 SLEAP version CLI tracking은 track_run.py에서 별도 실행.

import json
from pathlib import Path

import numpy as np
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
    QSizePolicy,
    QStyle, QStyleOptionSlider
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from .ui_mainwindow import Ui_MainWindow
from audio.data_load import load_audio, load_labels
from audio.aud_process import window_spectrogram
from video.track_data import process_tracking_data
from video.track_anchor import (
    select_anchor_frame, draw_anchor_frame, save_anchor_mapping
)
from voc_assign.aud_vid_sync import SyncConfig, sync_from_start_points
from voc_assign.ratio_corr import GeometryConfig, EmpiricalGeoConfig, EmpiricalThreshold
from voc_assign.voc_assign import run_assignment, run_empirical_assignment
from executor.result_viewer import format_results, export_results_csv, result_records


# Scientific intensity colormap: stronger log-magnitude -> strictly brighter.
# This is a display colormap for relative STFT levels, NOT measured SPL.
SPECTROGRAM_COLOR_STOPS = [
    (0.00, (8, 12, 19, 255)),      # near-black noise floor
    (0.17, (18, 39, 59, 255)),     # dark navy
    (0.36, (16, 84, 111, 255)),    # dark teal
    (0.56, (18, 146, 175, 255)),   # teal-cyan
    (0.76, (65, 207, 221, 255)),   # bright cyan
    (0.91, (178, 245, 222, 255)),  # pale mint
    (1.00, (255, 250, 220, 255)),  # strongest signal: pale cream
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
        self.label_path = None
        self.tracking_path = None
        # 두 매체의 동일 사건에 대응하는 기준점. None이면 아직 지정되지 않음.
        self.audio_start_s = None
        self.video_start_s = None
        self.sync = SyncConfig()
        self.has_video = False
        self.video_duration_ms = 0
        self.window_start_ms = 0

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
        self._setup_analysis_controls()
        self._connect_signals()

        # GitHub 최신 UI에 이미 고정된 두 개의 Audio 행을 재사용한다.
        self._rebuild_plots()
        self._refresh_audio_state()

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
            (self.ui.lbl_audio_path, self.ui.line_subject_id,
             self.ui.line_audio_path, self.ui.btn_audio_browse),
            (self.ui.lbl_audio_path_2, self.ui.line_subject_id_2,
             self.ui.line_audio_path_2, self.ui.btn_audio_browse_2),
        )
        for index, (label, subject, path, browse) in enumerate(fixed_rows):
            subject.setText(subject.text().strip() or f"mouse_{index+1}")
            path.setReadOnly(True)
            self.audio_rows.append({
                "panel": None, "label": label, "subject": subject,
                "path": path, "browse": browse, "delete": None
            })
            subject.editingFinished.connect(self._invalidate_results)

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
        self.graph_scroll.setWidgetResizable(True)
        self.graph_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.graph_host = self.ui.spectrogram_container
        self.graph_layout = self.graph_host.layout()
        if self.graph_layout is None:
            self.graph_layout = QVBoxLayout(self.graph_host)
        self.graph_layout.setContentsMargins(7, 8, 7, 8)
        self.graph_layout.setSpacing(8)

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
        self.ui.btn_play_all.setEnabled(False)

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

    def _setup_analysis_controls(self):
        """기존 settings_panel에 추적/캘리브레이션 및 결과 내보내기 버튼 추가."""
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

        self.geometry_button = QPushButton("Load geometry.json", self.ui.settings_panel)
        self.geometry_button.setToolTip("마이크 실측 좌표와 camera homography를 로드.")
        self.ui.verticalLayout_5.addWidget(self.geometry_button)
        self.label_geometry = QLabel("Geometry: none (ratio-only)", self.ui.settings_panel)
        self.ui.verticalLayout_5.addWidget(self.label_geometry)

        self.anchor_button = QPushButton("Review anchor IDs", self.ui.settings_panel)
        self.ui.verticalLayout_5.addWidget(self.anchor_button)

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
        self.ui.slider_timeline_audio.valueChanged.connect(self.draw_audio)
        self.ui.slider_timeline_video.sliderMoved.connect(self.player.setPosition)
        # 공통 타임라인은 두 Sync 기준점이 있으면 "동기화 시작점 이후 시간"을 의미.
        self.ui.slider_timeline_all.sliderMoved.connect(self._seek_common_timeline)
        self.player.durationChanged.connect(self._on_video_duration)
        self.player.positionChanged.connect(self.video_position)

        self.ui.spin_n_fft.valueChanged.connect(self._redraw_current_window)
        self.ui.spin_hop_length.valueChanged.connect(self._redraw_current_window)
        self.color_min_spin.valueChanged.connect(self._on_color_limits_changed)
        self.color_max_spin.valueChanged.connect(self._on_color_limits_changed)
        # Sync 버튼은 상대 매체로 탐색하는 대신 현재 기준점을 고정한다.
        self.ui.btn_starting_audio.clicked.connect(self.mark_audio_start)
        self.ui.btn_starting_video.clicked.connect(self.mark_video_start)
        self.reset_sync_button.clicked.connect(self.reset_sync_starts)
        self._refresh_sync_labels()
        self.geometry_button.clicked.connect(self.open_geometry)
        self.anchor_button.clicked.connect(self.review_anchor)
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
        label = QLabel(f"Audio {index+1}", panel)
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
            delete = QPushButton("×", panel)
            delete.setToolTip("Remove this audio channel")
            delete.setFixedWidth(27)
            row_layout.addWidget(delete)
        row_layout.addWidget(label)
        row_layout.addWidget(subject)
        row_layout.addWidget(path, stretch=1)
        row_layout.addWidget(browse)

        record = {"panel": panel, "label": label,
                  "subject": subject, "path": path,
                  "browse": browse, "delete": delete}
        browse.clicked.connect(
            lambda checked=False, current=record:
                self.open_audio_channel(self.audio_rows.index(current))
        )
        if delete:
            delete.clicked.connect(
                lambda checked=False, current=record:
                    self.remove_audio_channel(self.audio_rows.index(current))
            )
        subject.editingFinished.connect(self._invalidate_results)
        self.channel_layout.insertWidget(self.channel_layout.count()-1, panel)
        self.audio_rows.append(record)
        self.audio_slots.append(None)
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
        self._sync_channel_fields()
        self._on_channel_structure_changed()

    def _sync_channel_fields(self):
        """화면 순서와 AudioData.channel_id를 항상 같은 순서로 유지."""
        self.channel_fields = [entry["path"] for entry in self.audio_rows]
        self.subject_fields = [entry["subject"] for entry in self.audio_rows]
        for i, (row, audio) in enumerate(zip(self.audio_rows, self.audio_slots)):
            if i > 0:
                row["label"].setText(f"Audio {i+1}")
                row["subject"].setObjectName(f"subject_id_{i+1}")
            if audio is not None:
                audio.channel_id = f"ch{i+1}"

    def _on_channel_structure_changed(self):
        """기존 채널에 대한 geometry ID와 Sync 정보는 채널 구조 변경 시 무효."""
        if hasattr(self, "label_geometry"):
            self.geometry = None
            self.label_geometry.setText("Geometry: none (channel list changed)")
            self._clear_sync_starts()
        if hasattr(self, "graph_layout"):
            self._rebuild_plots()
            self._refresh_audio_state()
            self.draw_audio(0)

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
            panel = QWidget(self.graph_host)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(1, 1, 1, 1)
            title = f"ch{index+1} | {audio.path.name if audio else 'No WAV'}"
            layout.addWidget(QLabel(title, panel))
            plot = pg.PlotWidget(panel)
            plot.setMinimumHeight(178)
            plot.setBackground("#0A1119")
            plot.setLabel("bottom", "Time", units="s")
            plot.setLabel("left", "Frequency", units="Hz")
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen("#6C8797"))
                axis.setTextPen(pg.mkPen("#BACBD4"))
            plot.showGrid(x=True, y=True, alpha=0.13)
            image = pg.ImageItem(axisOrder="col-major")
            image.setLookupTable(self.spectrogram_lut)
            image.setLevels(self.spectrogram_levels_db)  # 생성부터 고정 레벨 적용
            plot.addItem(image)
            playhead = pg.InfiniteLine(angle=90, pen="y", movable=False)
            plot.addItem(playhead)
            playhead.setVisible(self._sync_is_ready())
            # 초록색 점선은 고정한 오디오 기준점으로, 타임라인 이동에 영향을 받지 않는다.
            anchor_line = pg.InfiniteLine(
                angle=90, pen=pg.mkPen((64, 201, 223), width=2,
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
            layout.addWidget(plot)
            self.graph_layout.addWidget(panel)
        self.graph_layout.addStretch()

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
        csv_sr = None
        if Path(path).suffix.lower() == ".csv":
            csv_sr, accepted = QInputDialog.getInt(
                self, "Sampling rate", "CSV waveform sampling rate (Hz):",
                250000, 1, 2000000, 1
            )
            if not accepted:
                return
        try:
            updated = list(self.audio_slots)
            updated[index] = load_audio(path, f"ch{index+1}", csv_sr=csv_sr)
            self._validate_slots(updated)
            self.audio_slots = updated
            self._apply_audio_update()
        except Exception as error:
            QMessageBox.critical(self, "Audio loading", str(error))

    def _apply_audio_update(self):
        for i, audio in enumerate(self.audio_slots):
            self.channel_fields[i].setText(str(audio.path) if audio else "")
        self.results = []
        self.export_button.setEnabled(False)
        self._clear_sync_starts()
        self._rebuild_plots()
        self._refresh_audio_state()
        self.draw_audio(0)

    def _refresh_audio_state(self):
        loaded = [a for a in self.audio_slots if a is not None]
        # ch1 경로창은 전체 상태 요약으로 덮어쓰지 않는다.
        self.statusBar().showMessage(
            f"Audio: {len(loaded)}/{len(self.audio_slots)} channels loaded", 3000
        )
        duration = min((a.duration_s for a in loaded), default=0.0)
        max_start = max(0, round((duration-self.VIEW_SECONDS)*1000))
        with QSignalBlocker(self.ui.slider_timeline_audio):
            self.ui.slider_timeline_audio.setRange(0, max_start)
            self.ui.slider_timeline_audio.setValue(0)
        self.window_start_ms = 0
        self.ui.lbl_time_audio.setText(
            f"00:00 / {self._format_ms(duration*1000)}"
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
        self.draw_audio(self.ui.slider_timeline_audio.value())

    def draw_audio(self, position_ms):
        """UI 이벤트는 계산 예약만 한다. 전용 워커가 모든 채널 STFT 실행."""
        self.window_start_ms = int(position_ms)
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
            playhead.setVisible(self._sync_is_ready())
            if self._sync_is_ready():
                playhead.setValue(self.sync.video_to_audio(self.player.position()/1000))

        start_s = self.window_start_ms / 1000
        if self.spectrogram_views:
            self.spectrogram_views[0][0].setXRange(
                start_s, start_s+self.VIEW_SECONDS, padding=0
            )
        # 개별 오디오 시계는 슬라이더가 가리키는 시작 시각/전체 길이.
        duration_s = min(
            (a.duration_s for a in self.audio_slots if a is not None), default=0.0
        )
        self.ui.lbl_time_audio.setText(
            f"{self._format_ms(start_s*1000)} / "
            f"{self._format_ms(duration_s*1000)}"
        )
        self._refresh_result_overlay()
        if getattr(self, "active_overlay_mode", None):
            self._refresh_result_overlay()

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Video", "", "Video (*.mp4 *.avi *.mov)"
        )
        if path:
            self.ui.line_video_path.setText(path)
            self._clear_sync_starts()
            self.player.setSource(QUrl.fromLocalFile(path))
            self.has_video = True
            self.ui.btn_play_all.setEnabled(True)
            self.ui.tab_content.setCurrentWidget(self.ui.tab_video)
            # 다른 영상을 선택하면 기존 tracking/anchor가 맞지 않으므로 해제.
            self.tracking_df = None
            self.anchor_mapping = None
            self.ui.line_tracking_file.clear()

    def open_label(self):
        """CSV/TXT 라벨 읽기; 헤더의 onset/offset은 '초' 단위를 기준으로 함."""
        path, _ = QFileDialog.getOpenFileName(
            self, "USV labels", "",
            "Label files (*.csv *.txt);;CSV (*.csv);;Text (*.txt);;All (*.*)"
        )
        if path:
            try:
                loaded = load_labels(path, unit="s")
                self.events = loaded
                self.label_path = path
                self._invalidate_results()
                self.ui.line_label_file.setText(path)
                self.statusBar().showMessage(f"Loaded {len(loaded)} USV intervals", 6000)
            except Exception as error:
                QMessageBox.critical(self, "USV labels", str(error))

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
            raw = load_tracking_table(path)  # CSV direct / .slp -> temporary CSV
            tracked = process_tracking_data(raw, fps)
            self.tracking_df = tracked
            self.tracking_path = path
            self.anchor_mapping = None
            self._invalidate_results()
            self.ui.line_tracking_file.setText(path)
            self.statusBar().showMessage(
                f"Tracking: {len(tracked)} rows, FPS={fps:.3f}", 6000
            )
            self.review_anchor()
        except Exception as error:
            QMessageBox.critical(self, "Tracking error", str(error))

    def review_anchor(self):
        """추적 ID를 실제 mouse_id에 연결하기 위한 영상 프레임 확인 대화상자."""
        if self.tracking_df is None:
            QMessageBox.information(self, "Anchor", "Tracking CSV를 먼저 불러오세요.")
            return
        try:
            frame_id = select_anchor_frame(self.tracking_df)
            frame_bgr = draw_anchor_frame(
                self.ui.line_video_path.text(), self.tracking_df, frame_id
            )
            rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
            height, width, _ = rgb.shape
            qimage = QImage(rgb.data, width, height, rgb.strides[0],
                            QImage.Format.Format_RGB888).copy()
        except Exception as error:
            QMessageBox.critical(self, "Anchor", str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Anchor frame #{frame_id}")
        layout = QVBoxLayout(dialog)
        preview = QLabel(dialog)
        preview.setPixmap(QPixmap.fromImage(qimage).scaled(
            1000, 460, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))
        layout.addWidget(preview)
        rows = self.tracking_df[self.tracking_df.frame_idx == frame_id]
        track_ids = [str(x) for x in rows.track_id.dropna().unique()]
        fields = {}
        form = QFormLayout()
        for i, track in enumerate(track_ids):
            editor = QLineEdit(dialog)
            editor.setPlaceholderText("Enter real mouse ID")
            editor.setText(
                self.anchor_mapping.get("track_to_mouse", {}).get(track, f"mouse_{i+1}")
                if self.anchor_mapping else f"mouse_{i+1}"
            )
            form.addRow(f"{track} ->", editor)
            fields[track] = editor
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            mapping = {track: editor.text().strip() for track, editor in fields.items()}
            self.anchor_mapping = save_anchor_mapping(mapping)
            self._invalidate_results()
            self.statusBar().showMessage(
                f"Anchor stored for {len(mapping)} video tracks", 6000
            )
        except Exception as error:
            QMessageBox.critical(self, "Anchor mapping", str(error))

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
        self.ui.btn_starting_audio.setToolTip("현재 오디오 슬라이더의 왼쪽 경계 시각을 시작점으로 고정/재지정.")
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
        self.audio_start_s = self.ui.slider_timeline_audio.value() / 1000.0
        self._recalculate_sync_from_anchors()

    def mark_video_start(self):
        """Video Sync: 현재 영상 시각을 고정하고 주황색 세로 마커로 표시."""
        if not self.has_video:
            QMessageBox.information(self, "Video Sync", "비디오 파일을 먼저 불러오세요.")
            return
        self.video_start_s = self.player.position() / 1000.0
        self._recalculate_sync_from_anchors()


    def run_analysis(self):
        """Default 탭: 영상과 무관하게 ratio-only 기준선 분석."""
        if self.analysis_busy:
            return
        if len(self.audio_slots) < 2 or any(a is None for a in self.audio_slots):
            QMessageBox.warning(self, "Audio", "오디오 파일이 모든 채널에 필요합니다.")
            return
        if not self.events:
            QMessageBox.warning(self, "Labels", "USV label CSV/TXT를 먼저 선택하세요.")
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
        self.statusBar().showMessage(f"{mode}: {len(rows)} labeled events analyzed", 8000)

    def _results_csv_text(self, results):
        """분석 결과를 CSV 행 형식으로 표시. subject는 채널 기반 후보일 수 있음."""
        import csv
        from io import StringIO
        buffer = StringIO()
        names = ["onset_s", "offset_s", "subject_id", "channel_id", "method", "reason"]
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

    def toggle_video(self):
        """하단 공통 Play/Pause: 영상과 N개 스펙트로그램의 시간 커서 동시 진행.

        초음파 원본 오디오의 가청화 재생은 별도 기능. 여기서는 영상 재생 시각을
        주 시계로 사용해 모든 STFT 그래프의 시각/표시창을 동기화한다.
        """
        if not self.has_video:
            QMessageBox.information(self, "Playback", "먼저 영상 파일을 불러오세요.")
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        start_ms, end_ms = self._common_playback_bounds_ms()
        if self._sync_is_ready() and (
            self.player.position() < start_ms or self.player.position() >= end_ms
        ):
            self.player.setPosition(start_ms)
        self.player.play()

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
        """하단 타임라인은 동기화 시작점을 0으로 표시하고 영상 위치로 변환."""
        start_ms, end_ms = self._common_playback_bounds_ms()
        self.player.setPosition(min(end_ms, start_ms + max(0, int(elapsed_ms))))

    @staticmethod
    def _format_ms(value_ms):
        seconds = max(0, int(value_ms) // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _on_video_duration(self, duration_ms):
        """개별 Video 길이와 두 매체가 겹치는 공통 길이를 따로 표시."""
        self.video_duration_ms = max(0, int(duration_ms))
        with QSignalBlocker(self.ui.slider_timeline_video):
            self.ui.slider_timeline_video.setRange(0, self.video_duration_ms)
        start_ms, end_ms = self._common_playback_bounds_ms()
        with QSignalBlocker(self.ui.slider_timeline_all):
            self.ui.slider_timeline_all.setRange(0, max(0, end_ms-start_ms))
        self.ui.lbl_time_video.setText(
            f"00:00 / {self._format_ms(self.video_duration_ms)}"
        )
        self.ui.label_time_all.setText(
            f"00:00 / {self._format_ms(max(0, end_ms-start_ms))}"
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
        if (self._sync_is_ready() and end_ms > start_ms
            and position_ms >= end_ms
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self.player.pause()

        # Audio와 Video 기준점을 모두 선택하기 전에는 연동하지 않는다.
        if not self._sync_is_ready():
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
        # N개 그래프를 매 30ms마다 다시 그리지 않고 3초창 경계에서만 요청.
        if any(a is not None for a in self.audio_slots):
            last_visible = self.window_start_ms + round(self.VIEW_SECONDS * 850)
            if audio_ms < self.window_start_ms or audio_ms >= last_visible:
                value = min(
                    max(0, audio_ms - round(self.VIEW_SECONDS * 200)),
                    self.ui.slider_timeline_audio.maximum()
                )
                if value != self.ui.slider_timeline_audio.value():
                    self.ui.slider_timeline_audio.setValue(value)

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
        if not self.events:
            missing.append("USV labels")
        if self.tracking_df is None:
            missing.append("SLEAP tracking CSV/.slp")
        if not self.anchor_mapping:
            missing.append("manual anchor IDs")
        if not self._sync_is_ready():
            missing.append("both Audio/Video Sync markers")
        if not self.geometry or not self.empirical_cfg or not self.empirical_threshold:
            missing.append("measured geometry + trained median/MAD JSON")
        if missing:
            QMessageBox.warning(self, "Experimental model", "Required: " + ", ".join(missing))
            return
        try:
            subjects = self.channel_subjects()
            if set(subjects.values()) != set(self.anchor_mapping["track_to_mouse"].values()):
                raise ValueError("Audio subject IDs must match both Video anchor mouse IDs.")
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
            dataframe = pd.read_csv(path)
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
            ground = pd.read_csv(path)
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
                 "subject_id": self.subject_fields[i].text().strip()}
                for i, audio in enumerate(self.audio_slots)
            ],
            "video": self.ui.line_video_path.text().strip() or None,
            "label": self.label_path,
            "tracking": self.tracking_path,
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
            paths = [row.get("path") for row in audio_rows]
            other_paths = [payload.get(key) for key in ("video", "label", "tracking", "geometry")]
            for source in paths + other_paths:
                if source and not Path(source).is_file():
                    raise FileNotFoundError(f"Referenced file not found: {source}")
            candidate = [
                load_audio(row["path"], f"ch{i+1}", csv_sr=row.get("sr"))
                if row.get("path") else None
                for i, row in enumerate(audio_rows)
            ]
            self._validate_slots(candidate)

            while len(self.audio_rows) < len(audio_rows):
                self.add_audio_channel()
            while len(self.audio_rows) > len(audio_rows):
                self.remove_audio_channel(len(self.audio_rows)-1)

            self.audio_slots = candidate
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

            self.label_path = payload.get("label")
            self.events = load_labels(self.label_path) if self.label_path else []
            self.ui.line_label_file.setText(self.label_path or "")

            self.tracking_path = payload.get("tracking")
            if self.tracking_path:
                import cv2
                capture = cv2.VideoCapture(video)
                fps = capture.get(cv2.CAP_PROP_FPS)
                capture.release()
                self.tracking_df = process_tracking_data(
                    load_tracking_table(self.tracking_path), fps
                )
                self.ui.line_tracking_file.setText(self.tracking_path)
            else:
                self.tracking_df = None
                self.ui.line_tracking_file.clear()

            self.anchor_mapping = payload.get("anchor_mapping")
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
            self.draw_audio(position)
            self.statusBar().showMessage(f"Session loaded: {path}", 7000)
        except Exception as error:
            QMessageBox.critical(self, "Session load", str(error))

    # 기존 Sync 탐색 함수는 mark_audio_start / mark_video_start로 대체했다.
    # 두 기준점을 고정한 이후에는 공통 Play와 영상 타임라인 이동이
    # 모든 오디오 스펙트로그램의 재생 커서를 같은 동기 시간으로 이동시킨다.

# 남은 연구 과제: USV 자체의 가청화, 하드웨어 동기 사건 추출, 개체 교차 ID 검증,
# 실측 거리/각도 파라미터 학습 및 holdout validation, 배포 환경 codecs/driver 테스트.

