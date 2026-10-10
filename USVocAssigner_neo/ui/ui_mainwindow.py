# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'mainwindow.ui'
##
## Created by: Qt User Interface Compiler version 6.11.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QMainWindow, QMenuBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QStatusBar, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1000, 800)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(MainWindow.sizePolicy().hasHeightForWidth())
        MainWindow.setSizePolicy(sizePolicy)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.verticalLayout_6 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_6.setSpacing(6)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.verticalLayout_6.setContentsMargins(15, 15, 15, 15)
        self.group_btn_file = QHBoxLayout()
        self.group_btn_file.setObjectName(u"group_btn_file")
        self.btn_save_process = QPushButton(self.centralwidget)
        self.btn_save_process.setObjectName(u"btn_save_process")

        self.group_btn_file.addWidget(self.btn_save_process)

        self.btn_open_file = QPushButton(self.centralwidget)
        self.btn_open_file.setObjectName(u"btn_open_file")

        self.group_btn_file.addWidget(self.btn_open_file)

        self.btn_save_csv = QPushButton(self.centralwidget)
        self.btn_save_csv.setObjectName(u"btn_save_csv")

        self.group_btn_file.addWidget(self.btn_save_csv)

        self.btn_save_pdf = QPushButton(self.centralwidget)
        self.btn_save_pdf.setObjectName(u"btn_save_pdf")

        self.group_btn_file.addWidget(self.btn_save_pdf)


        self.verticalLayout_6.addLayout(self.group_btn_file)

        self.file_panel = QGroupBox(self.centralwidget)
        self.file_panel.setObjectName(u"file_panel")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.file_panel.sizePolicy().hasHeightForWidth())
        self.file_panel.setSizePolicy(sizePolicy1)
        self.gridLayout = QGridLayout(self.file_panel)
        self.gridLayout.setSpacing(10)
        self.gridLayout.setObjectName(u"gridLayout")
        self.gridLayout.setContentsMargins(10, 10, 10, 10)
        self.line_audio_path = QLineEdit(self.file_panel)
        self.line_audio_path.setObjectName(u"line_audio_path")
        sizePolicy1.setHeightForWidth(self.line_audio_path.sizePolicy().hasHeightForWidth())
        self.line_audio_path.setSizePolicy(sizePolicy1)

        self.gridLayout.addWidget(self.line_audio_path, 0, 2, 1, 1)

        self.lbl_video_file = QLabel(self.file_panel)
        self.lbl_video_file.setObjectName(u"lbl_video_file")
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.lbl_video_file.sizePolicy().hasHeightForWidth())
        self.lbl_video_file.setSizePolicy(sizePolicy2)

        self.gridLayout.addWidget(self.lbl_video_file, 1, 0, 1, 1)

        self.lbl_audio_file = QLabel(self.file_panel)
        self.lbl_audio_file.setObjectName(u"lbl_audio_file")
        sizePolicy2.setHeightForWidth(self.lbl_audio_file.sizePolicy().hasHeightForWidth())
        self.lbl_audio_file.setSizePolicy(sizePolicy2)

        self.gridLayout.addWidget(self.lbl_audio_file, 0, 0, 1, 1)

        self.line_video_path = QLineEdit(self.file_panel)
        self.line_video_path.setObjectName(u"line_video_path")
        sizePolicy1.setHeightForWidth(self.line_video_path.sizePolicy().hasHeightForWidth())
        self.line_video_path.setSizePolicy(sizePolicy1)

        self.gridLayout.addWidget(self.line_video_path, 1, 2, 1, 1)

        self.btn_audio_browse = QPushButton(self.file_panel)
        self.btn_audio_browse.setObjectName(u"btn_audio_browse")

        self.gridLayout.addWidget(self.btn_audio_browse, 0, 7, 1, 2)

        self.btn_video_browse = QPushButton(self.file_panel)
        self.btn_video_browse.setObjectName(u"btn_video_browse")

        self.gridLayout.addWidget(self.btn_video_browse, 1, 7, 1, 2)


        self.verticalLayout_6.addWidget(self.file_panel)

        self.work_space = QWidget(self.centralwidget)
        self.work_space.setObjectName(u"work_space")
        sizePolicy3 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy3.setHorizontalStretch(0)
        sizePolicy3.setVerticalStretch(0)
        sizePolicy3.setHeightForWidth(self.work_space.sizePolicy().hasHeightForWidth())
        self.work_space.setSizePolicy(sizePolicy3)
        self.horizontalLayout = QHBoxLayout(self.work_space)
        self.horizontalLayout.setSpacing(1)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(self.work_space)
        self.splitter.setObjectName(u"splitter")
        sizePolicy3.setHeightForWidth(self.splitter.sizePolicy().hasHeightForWidth())
        self.splitter.setSizePolicy(sizePolicy3)
        self.splitter.setOrientation(Qt.Orientation.Horizontal)
        self.content_container = QWidget(self.splitter)
        self.content_container.setObjectName(u"content_container")
        sizePolicy.setHeightForWidth(self.content_container.sizePolicy().hasHeightForWidth())
        self.content_container.setSizePolicy(sizePolicy)
        self.content_container.setMinimumSize(QSize(600, 0))
        self.verticalLayout_3 = QVBoxLayout(self.content_container)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.tab_content = QTabWidget(self.content_container)
        self.tab_content.setObjectName(u"tab_content")
        self.tab_audio = QWidget()
        self.tab_audio.setObjectName(u"tab_audio")
        self.verticalLayout_9 = QVBoxLayout(self.tab_audio)
        self.verticalLayout_9.setObjectName(u"verticalLayout_9")
        self.scrollarea_spectrogram = QScrollArea(self.tab_audio)
        self.scrollarea_spectrogram.setObjectName(u"scrollarea_spectrogram")
        sizePolicy.setHeightForWidth(self.scrollarea_spectrogram.sizePolicy().hasHeightForWidth())
        self.scrollarea_spectrogram.setSizePolicy(sizePolicy)
        self.scrollarea_spectrogram.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollarea_spectrogram.setWidgetResizable(True)
        self.scrollAreaWidgetContents = QWidget()
        self.scrollAreaWidgetContents.setObjectName(u"scrollAreaWidgetContents")
        self.scrollAreaWidgetContents.setGeometry(QRect(0, 0, 554, 412))
        self.horizontalLayout_2 = QHBoxLayout(self.scrollAreaWidgetContents)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.spectrogram_container = QWidget(self.scrollAreaWidgetContents)
        self.spectrogram_container.setObjectName(u"spectrogram_container")
        sizePolicy4 = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        sizePolicy4.setHorizontalStretch(0)
        sizePolicy4.setVerticalStretch(0)
        sizePolicy4.setHeightForWidth(self.spectrogram_container.sizePolicy().hasHeightForWidth())
        self.spectrogram_container.setSizePolicy(sizePolicy4)

        self.horizontalLayout_2.addWidget(self.spectrogram_container)

        self.scrollarea_spectrogram.setWidget(self.scrollAreaWidgetContents)

        self.verticalLayout_9.addWidget(self.scrollarea_spectrogram)

        self.playback_panel_audio = QGroupBox(self.tab_audio)
        self.playback_panel_audio.setObjectName(u"playback_panel_audio")
        sizePolicy1.setHeightForWidth(self.playback_panel_audio.sizePolicy().hasHeightForWidth())
        self.playback_panel_audio.setSizePolicy(sizePolicy1)
        self.horizontalLayout_4 = QHBoxLayout(self.playback_panel_audio)
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.btn_starting_audio = QPushButton(self.playback_panel_audio)
        self.btn_starting_audio.setObjectName(u"btn_starting_audio")
        sizePolicy5 = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sizePolicy5.setHorizontalStretch(0)
        sizePolicy5.setVerticalStretch(0)
        sizePolicy5.setHeightForWidth(self.btn_starting_audio.sizePolicy().hasHeightForWidth())
        self.btn_starting_audio.setSizePolicy(sizePolicy5)

        self.horizontalLayout_4.addWidget(self.btn_starting_audio)

        self.slider_timeline_audio = QSlider(self.playback_panel_audio)
        self.slider_timeline_audio.setObjectName(u"slider_timeline_audio")
        self.slider_timeline_audio.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_4.addWidget(self.slider_timeline_audio)

        self.lbl_time_audio = QLabel(self.playback_panel_audio)
        self.lbl_time_audio.setObjectName(u"lbl_time_audio")
        sizePolicy5.setHeightForWidth(self.lbl_time_audio.sizePolicy().hasHeightForWidth())
        self.lbl_time_audio.setSizePolicy(sizePolicy5)
        self.lbl_time_audio.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_4.addWidget(self.lbl_time_audio)


        self.verticalLayout_9.addWidget(self.playback_panel_audio)

        self.tab_content.addTab(self.tab_audio, "")
        self.tab_video = QWidget()
        self.tab_video.setObjectName(u"tab_video")
        self.verticalLayout_7 = QVBoxLayout(self.tab_video)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.video_container = QWidget(self.tab_video)
        self.video_container.setObjectName(u"video_container")
        sizePolicy.setHeightForWidth(self.video_container.sizePolicy().hasHeightForWidth())
        self.video_container.setSizePolicy(sizePolicy)

        self.verticalLayout_7.addWidget(self.video_container)

        self.playback_panel_4 = QGroupBox(self.tab_video)
        self.playback_panel_4.setObjectName(u"playback_panel_4")
        sizePolicy1.setHeightForWidth(self.playback_panel_4.sizePolicy().hasHeightForWidth())
        self.playback_panel_4.setSizePolicy(sizePolicy1)
        self.horizontalLayout_5 = QHBoxLayout(self.playback_panel_4)
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.btn_starting_video = QPushButton(self.playback_panel_4)
        self.btn_starting_video.setObjectName(u"btn_starting_video")
        sizePolicy5.setHeightForWidth(self.btn_starting_video.sizePolicy().hasHeightForWidth())
        self.btn_starting_video.setSizePolicy(sizePolicy5)

        self.horizontalLayout_5.addWidget(self.btn_starting_video)

        self.slider_timeline_video = QSlider(self.playback_panel_4)
        self.slider_timeline_video.setObjectName(u"slider_timeline_video")
        self.slider_timeline_video.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_5.addWidget(self.slider_timeline_video)

        self.lbl_time_video = QLabel(self.playback_panel_4)
        self.lbl_time_video.setObjectName(u"lbl_time_video")
        sizePolicy5.setHeightForWidth(self.lbl_time_video.sizePolicy().hasHeightForWidth())
        self.lbl_time_video.setSizePolicy(sizePolicy5)
        self.lbl_time_video.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_5.addWidget(self.lbl_time_video)


        self.verticalLayout_7.addWidget(self.playback_panel_4)

        self.tab_content.addTab(self.tab_video, "")

        self.verticalLayout_3.addWidget(self.tab_content)

        self.splitter.addWidget(self.content_container)
        self.settings_panel = QWidget(self.splitter)
        self.settings_panel.setObjectName(u"settings_panel")
        sizePolicy4.setHeightForWidth(self.settings_panel.sizePolicy().hasHeightForWidth())
        self.settings_panel.setSizePolicy(sizePolicy4)
        self.settings_panel.setMinimumSize(QSize(300, 0))
        self.verticalLayout = QVBoxLayout(self.settings_panel)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.tab_analyze = QTabWidget(self.settings_panel)
        self.tab_analyze.setObjectName(u"tab_analyze")
        self.tab_default_analyze = QWidget()
        self.tab_default_analyze.setObjectName(u"tab_default_analyze")
        self.verticalLayout_8 = QVBoxLayout(self.tab_default_analyze)
        self.verticalLayout_8.setObjectName(u"verticalLayout_8")
        self.verticalLayout_5 = QVBoxLayout()
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.verticalLayout_2 = QVBoxLayout()
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.lbl_analysis_file = QLabel(self.tab_default_analyze)
        self.lbl_analysis_file.setObjectName(u"lbl_analysis_file")
        sizePolicy5.setHeightForWidth(self.lbl_analysis_file.sizePolicy().hasHeightForWidth())
        self.lbl_analysis_file.setSizePolicy(sizePolicy5)

        self.verticalLayout_2.addWidget(self.lbl_analysis_file)

        self.gridLayout_2 = QGridLayout()
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.lbl_label_file = QLabel(self.tab_default_analyze)
        self.lbl_label_file.setObjectName(u"lbl_label_file")
        sizePolicy6 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sizePolicy6.setHorizontalStretch(0)
        sizePolicy6.setVerticalStretch(0)
        sizePolicy6.setHeightForWidth(self.lbl_label_file.sizePolicy().hasHeightForWidth())
        self.lbl_label_file.setSizePolicy(sizePolicy6)

        self.gridLayout_2.addWidget(self.lbl_label_file, 0, 0, 1, 1)

        self.line_label_file = QLineEdit(self.tab_default_analyze)
        self.line_label_file.setObjectName(u"line_label_file")

        self.gridLayout_2.addWidget(self.line_label_file, 0, 1, 1, 1)

        self.btn_label_browse = QPushButton(self.tab_default_analyze)
        self.btn_label_browse.setObjectName(u"btn_label_browse")

        self.gridLayout_2.addWidget(self.btn_label_browse, 0, 2, 1, 1)

        self.lbl_tracking_file = QLabel(self.tab_default_analyze)
        self.lbl_tracking_file.setObjectName(u"lbl_tracking_file")

        self.gridLayout_2.addWidget(self.lbl_tracking_file, 1, 0, 1, 1)

        self.line_tracking_file = QLineEdit(self.tab_default_analyze)
        self.line_tracking_file.setObjectName(u"line_tracking_file")

        self.gridLayout_2.addWidget(self.line_tracking_file, 1, 1, 1, 1)

        self.lbl_tracking_browse = QPushButton(self.tab_default_analyze)
        self.lbl_tracking_browse.setObjectName(u"lbl_tracking_browse")

        self.gridLayout_2.addWidget(self.lbl_tracking_browse, 1, 2, 1, 1)


        self.verticalLayout_2.addLayout(self.gridLayout_2)


        self.verticalLayout_5.addLayout(self.verticalLayout_2)

        self.verticalLayout_4 = QVBoxLayout()
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.lbl_analysis_settings = QLabel(self.tab_default_analyze)
        self.lbl_analysis_settings.setObjectName(u"lbl_analysis_settings")
        sizePolicy2.setHeightForWidth(self.lbl_analysis_settings.sizePolicy().hasHeightForWidth())
        self.lbl_analysis_settings.setSizePolicy(sizePolicy2)

        self.verticalLayout_4.addWidget(self.lbl_analysis_settings)

        self.formLayout = QFormLayout()
        self.formLayout.setObjectName(u"formLayout")
        self.formLayout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.formLayout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.formLayout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.spin_n_fft = QSpinBox(self.tab_default_analyze)
        self.spin_n_fft.setObjectName(u"spin_n_fft")
        self.spin_n_fft.setMinimum(16)
        self.spin_n_fft.setMaximum(131072)
        self.spin_n_fft.setValue(2048)

        self.formLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.spin_n_fft)

        self.lbl_hop_length = QLabel(self.tab_default_analyze)
        self.lbl_hop_length.setObjectName(u"lbl_hop_length")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.lbl_hop_length)

        self.spin_hop_length = QSpinBox(self.tab_default_analyze)
        self.spin_hop_length.setObjectName(u"spin_hop_length")
        self.spin_hop_length.setMinimum(1)
        self.spin_hop_length.setMaximum(65536)
        self.spin_hop_length.setValue(512)

        self.formLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.spin_hop_length)

        self.lbl_n_fft = QLabel(self.tab_default_analyze)
        self.lbl_n_fft.setObjectName(u"lbl_n_fft")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.lbl_n_fft)


        self.verticalLayout_4.addLayout(self.formLayout)


        self.verticalLayout_5.addLayout(self.verticalLayout_4)


        self.verticalLayout_8.addLayout(self.verticalLayout_5)

        self.horizontalLayout_6 = QHBoxLayout()
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.btn_plot_spectrogram = QPushButton(self.tab_default_analyze)
        self.btn_plot_spectrogram.setObjectName(u"btn_plot_spectrogram")

        self.horizontalLayout_6.addWidget(self.btn_plot_spectrogram)

        self.btn_run_analysis = QPushButton(self.tab_default_analyze)
        self.btn_run_analysis.setObjectName(u"btn_run_analysis")

        self.horizontalLayout_6.addWidget(self.btn_run_analysis)


        self.verticalLayout_8.addLayout(self.horizontalLayout_6)

        self.horizontalLayout_12 = QHBoxLayout()
        self.horizontalLayout_12.setObjectName(u"horizontalLayout_12")
        self.lbl_result = QLabel(self.tab_default_analyze)
        self.lbl_result.setObjectName(u"lbl_result")

        self.horizontalLayout_12.addWidget(self.lbl_result)

        self.btn_show_default = QPushButton(self.tab_default_analyze)
        self.btn_show_default.setObjectName(u"btn_show_default")

        self.horizontalLayout_12.addWidget(self.btn_show_default)


        self.verticalLayout_8.addLayout(self.horizontalLayout_12)

        self.scrollarea_result = QScrollArea(self.tab_default_analyze)
        self.scrollarea_result.setObjectName(u"scrollarea_result")
        sizePolicy4.setHeightForWidth(self.scrollarea_result.sizePolicy().hasHeightForWidth())
        self.scrollarea_result.setSizePolicy(sizePolicy4)
        self.scrollarea_result.setAutoFillBackground(True)
        self.scrollarea_result.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollarea_result.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.scrollarea_result.setWidgetResizable(True)
        self.scrollAreaWidgetContents_2 = QWidget()
        self.scrollAreaWidgetContents_2.setObjectName(u"scrollAreaWidgetContents_2")
        self.scrollAreaWidgetContents_2.setGeometry(QRect(0, 0, 319, 216))
        self.verticalLayout_13 = QVBoxLayout(self.scrollAreaWidgetContents_2)
        self.verticalLayout_13.setObjectName(u"verticalLayout_13")
        self.text_result = QTextEdit(self.scrollAreaWidgetContents_2)
        self.text_result.setObjectName(u"text_result")
        sizePolicy4.setHeightForWidth(self.text_result.sizePolicy().hasHeightForWidth())
        self.text_result.setSizePolicy(sizePolicy4)

        self.verticalLayout_13.addWidget(self.text_result)

        self.scrollarea_result.setWidget(self.scrollAreaWidgetContents_2)

        self.verticalLayout_8.addWidget(self.scrollarea_result)

        self.tab_analyze.addTab(self.tab_default_analyze, "")
        self.tab_test_analyze = QWidget()
        self.tab_test_analyze.setObjectName(u"tab_test_analyze")
        self.verticalLayout_10 = QVBoxLayout(self.tab_test_analyze)
        self.verticalLayout_10.setObjectName(u"verticalLayout_10")
        self.group_distance_corr = QGroupBox(self.tab_test_analyze)
        self.group_distance_corr.setObjectName(u"group_distance_corr")
        sizePolicy3.setHeightForWidth(self.group_distance_corr.sizePolicy().hasHeightForWidth())
        self.group_distance_corr.setSizePolicy(sizePolicy3)
        self.verticalLayout_11 = QVBoxLayout(self.group_distance_corr)
        self.verticalLayout_11.setObjectName(u"verticalLayout_11")
        self.horizontalLayout_7 = QHBoxLayout()
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.btn_distance_corr = QPushButton(self.group_distance_corr)
        self.btn_distance_corr.setObjectName(u"btn_distance_corr")

        self.horizontalLayout_7.addWidget(self.btn_distance_corr)

        self.btn_dist_show = QPushButton(self.group_distance_corr)
        self.btn_dist_show.setObjectName(u"btn_dist_show")

        self.horizontalLayout_7.addWidget(self.btn_dist_show)


        self.verticalLayout_11.addLayout(self.horizontalLayout_7)

        self.lbl_dist_corr = QLabel(self.group_distance_corr)
        self.lbl_dist_corr.setObjectName(u"lbl_dist_corr")

        self.verticalLayout_11.addWidget(self.lbl_dist_corr)

        self.scrollarea_dist_result = QScrollArea(self.group_distance_corr)
        self.scrollarea_dist_result.setObjectName(u"scrollarea_dist_result")
        sizePolicy3.setHeightForWidth(self.scrollarea_dist_result.sizePolicy().hasHeightForWidth())
        self.scrollarea_dist_result.setSizePolicy(sizePolicy3)
        self.scrollarea_dist_result.setAutoFillBackground(True)
        self.scrollarea_dist_result.setWidgetResizable(True)
        self.scrollAreaWidgetContents_3 = QWidget()
        self.scrollAreaWidgetContents_3.setObjectName(u"scrollAreaWidgetContents_3")
        self.scrollAreaWidgetContents_3.setGeometry(QRect(0, 0, 297, 134))
        self.horizontalLayout_11 = QHBoxLayout(self.scrollAreaWidgetContents_3)
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.text_dist_result = QTextEdit(self.scrollAreaWidgetContents_3)
        self.text_dist_result.setObjectName(u"text_dist_result")
        sizePolicy3.setHeightForWidth(self.text_dist_result.sizePolicy().hasHeightForWidth())
        self.text_dist_result.setSizePolicy(sizePolicy3)

        self.horizontalLayout_11.addWidget(self.text_dist_result)

        self.scrollarea_dist_result.setWidget(self.scrollAreaWidgetContents_3)

        self.verticalLayout_11.addWidget(self.scrollarea_dist_result)


        self.verticalLayout_10.addWidget(self.group_distance_corr)

        self.group_geo_corr = QGroupBox(self.tab_test_analyze)
        self.group_geo_corr.setObjectName(u"group_geo_corr")
        sizePolicy3.setHeightForWidth(self.group_geo_corr.sizePolicy().hasHeightForWidth())
        self.group_geo_corr.setSizePolicy(sizePolicy3)
        self.verticalLayout_12 = QVBoxLayout(self.group_geo_corr)
        self.verticalLayout_12.setObjectName(u"verticalLayout_12")
        self.horizontalLayout_8 = QHBoxLayout()
        self.horizontalLayout_8.setObjectName(u"horizontalLayout_8")
        self.btn_geo_corr = QPushButton(self.group_geo_corr)
        self.btn_geo_corr.setObjectName(u"btn_geo_corr")

        self.horizontalLayout_8.addWidget(self.btn_geo_corr)

        self.btn_geo_show = QPushButton(self.group_geo_corr)
        self.btn_geo_show.setObjectName(u"btn_geo_show")

        self.horizontalLayout_8.addWidget(self.btn_geo_show)


        self.verticalLayout_12.addLayout(self.horizontalLayout_8)

        self.lbl_geo_corr = QLabel(self.group_geo_corr)
        self.lbl_geo_corr.setObjectName(u"lbl_geo_corr")

        self.verticalLayout_12.addWidget(self.lbl_geo_corr)

        self.scrollarea_geo_result = QScrollArea(self.group_geo_corr)
        self.scrollarea_geo_result.setObjectName(u"scrollarea_geo_result")
        self.scrollarea_geo_result.setAutoFillBackground(True)
        self.scrollarea_geo_result.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollarea_geo_result.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.scrollarea_geo_result.setWidgetResizable(True)
        self.scrollarea_geo_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scrollAreaWidgetContents_4 = QWidget()
        self.scrollAreaWidgetContents_4.setObjectName(u"scrollAreaWidgetContents_4")
        self.scrollAreaWidgetContents_4.setGeometry(QRect(0, 0, 297, 134))
        self.horizontalLayout_10 = QHBoxLayout(self.scrollAreaWidgetContents_4)
        self.horizontalLayout_10.setObjectName(u"horizontalLayout_10")
        self.text_geo_result = QTextEdit(self.scrollAreaWidgetContents_4)
        self.text_geo_result.setObjectName(u"text_geo_result")

        self.horizontalLayout_10.addWidget(self.text_geo_result)

        self.scrollarea_geo_result.setWidget(self.scrollAreaWidgetContents_4)

        self.verticalLayout_12.addWidget(self.scrollarea_geo_result)


        self.verticalLayout_10.addWidget(self.group_geo_corr)

        self.tab_analyze.addTab(self.tab_test_analyze, "")

        self.verticalLayout.addWidget(self.tab_analyze)

        self.splitter.addWidget(self.settings_panel)

        self.horizontalLayout.addWidget(self.splitter)


        self.verticalLayout_6.addWidget(self.work_space)

        self.playback_panel_all = QGroupBox(self.centralwidget)
        self.playback_panel_all.setObjectName(u"playback_panel_all")
        sizePolicy1.setHeightForWidth(self.playback_panel_all.sizePolicy().hasHeightForWidth())
        self.playback_panel_all.setSizePolicy(sizePolicy1)
        self.horizontalLayout_3 = QHBoxLayout(self.playback_panel_all)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.btn_play_all = QPushButton(self.playback_panel_all)
        self.btn_play_all.setObjectName(u"btn_play_all")
        sizePolicy5.setHeightForWidth(self.btn_play_all.sizePolicy().hasHeightForWidth())
        self.btn_play_all.setSizePolicy(sizePolicy5)

        self.horizontalLayout_3.addWidget(self.btn_play_all)

        self.slider_timeline_all = QSlider(self.playback_panel_all)
        self.slider_timeline_all.setObjectName(u"slider_timeline_all")
        self.slider_timeline_all.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_3.addWidget(self.slider_timeline_all)

        self.label_time_all = QLabel(self.playback_panel_all)
        self.label_time_all.setObjectName(u"label_time_all")
        sizePolicy5.setHeightForWidth(self.label_time_all.sizePolicy().hasHeightForWidth())
        self.label_time_all.setSizePolicy(sizePolicy5)
        self.label_time_all.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_3.addWidget(self.label_time_all)


        self.verticalLayout_6.addWidget(self.playback_panel_all)

        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 1000, 33))
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)

        self.tab_content.setCurrentIndex(1)
        self.tab_analyze.setCurrentIndex(1)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"USVocAssigner", None))
        self.btn_save_process.setText(QCoreApplication.translate("MainWindow", u"Save the Process", None))
        self.btn_open_file.setText(QCoreApplication.translate("MainWindow", u"Open File...", None))
        self.btn_save_csv.setText(QCoreApplication.translate("MainWindow", u"Save as CSV", None))
        self.btn_save_pdf.setText(QCoreApplication.translate("MainWindow", u"Save as PDF", None))
        self.lbl_video_file.setText(QCoreApplication.translate("MainWindow", u"Video File", None))
        self.lbl_audio_file.setText(QCoreApplication.translate("MainWindow", u"Audio File", None))
        self.btn_audio_browse.setText(QCoreApplication.translate("MainWindow", u"Browse", None))
        self.btn_video_browse.setText(QCoreApplication.translate("MainWindow", u"Browse", None))
        self.btn_starting_audio.setText(QCoreApplication.translate("MainWindow", u"Sync", None))
        self.lbl_time_audio.setText(QCoreApplication.translate("MainWindow", u"00:00 / 00:00", None))
        self.tab_content.setTabText(self.tab_content.indexOf(self.tab_audio), QCoreApplication.translate("MainWindow", u"Audio Spectrogram", None))
        self.btn_starting_video.setText(QCoreApplication.translate("MainWindow", u"Sync", None))
        self.lbl_time_video.setText(QCoreApplication.translate("MainWindow", u"00:00 / 00:00", None))
        self.tab_content.setTabText(self.tab_content.indexOf(self.tab_video), QCoreApplication.translate("MainWindow", u"Video", None))
        self.lbl_analysis_file.setText(QCoreApplication.translate("MainWindow", u"Analysis File", None))
        self.lbl_label_file.setText(QCoreApplication.translate("MainWindow", u"Vocalization Label File", None))
        self.btn_label_browse.setText(QCoreApplication.translate("MainWindow", u"Browse", None))
        self.lbl_tracking_file.setText(QCoreApplication.translate("MainWindow", u"Video Tracking File", None))
        self.lbl_tracking_browse.setText(QCoreApplication.translate("MainWindow", u"Browse", None))
        self.lbl_analysis_settings.setText(QCoreApplication.translate("MainWindow", u"Analysis Settings", None))
        self.lbl_hop_length.setText(QCoreApplication.translate("MainWindow", u"hop_length", None))
        self.lbl_n_fft.setText(QCoreApplication.translate("MainWindow", u"n_fft", None))
        self.btn_plot_spectrogram.setText(QCoreApplication.translate("MainWindow", u"Plot Spectrograms", None))
        self.btn_run_analysis.setText(QCoreApplication.translate("MainWindow", u"Run Assignment", None))
        self.lbl_result.setText(QCoreApplication.translate("MainWindow", u"Assignment Result", None))
        self.btn_show_default.setText(QCoreApplication.translate("MainWindow", u"Show on Graph", None))
        self.tab_analyze.setTabText(self.tab_analyze.indexOf(self.tab_default_analyze), QCoreApplication.translate("MainWindow", u"Default Analyzing", None))
        self.group_distance_corr.setTitle(QCoreApplication.translate("MainWindow", u"Distance Correction", None))
        self.btn_distance_corr.setText(QCoreApplication.translate("MainWindow", u"Run Analysis", None))
        self.btn_dist_show.setText(QCoreApplication.translate("MainWindow", u"Show on Graph", None))
        self.lbl_dist_corr.setText(QCoreApplication.translate("MainWindow", u"Assignment Result", None))
        self.group_geo_corr.setTitle(QCoreApplication.translate("MainWindow", u"Geometrical Correction", None))
        self.btn_geo_corr.setText(QCoreApplication.translate("MainWindow", u"Run Analysis", None))
        self.btn_geo_show.setText(QCoreApplication.translate("MainWindow", u"Show on Graph", None))
        self.lbl_geo_corr.setText(QCoreApplication.translate("MainWindow", u"Assignment Result", None))
        self.tab_analyze.setTabText(self.tab_analyze.indexOf(self.tab_test_analyze), QCoreApplication.translate("MainWindow", u"Test Analyzing (optional)", None))
        self.btn_play_all.setText(QCoreApplication.translate("MainWindow", u"\u25b6", None))
        self.label_time_all.setText(QCoreApplication.translate("MainWindow", u"00:00 / 00:00", None))
    # retranslateUi

