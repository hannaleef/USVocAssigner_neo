# [vocassign/exam_assign.py] 인공 사인파를 이용한 간단한 smoke test
# A채널과 B채널에 같은 50kHz 파형을 다른 진폭으로 주입한다.
# 강한 A채널이 선택되는지만 점검; 실제 USV 판별 성능 검증이 아니다.
# 검증 범위 확장: 무음, 매우 짧은 이벤트, 동시발성, 서로 다른 sampling rate.

import numpy as np
from audio.data_load import AudioData, USVEvent
from .voc_assign import ratio_only_assignment

# 알고리즘이 가장 기본적인 채널 순위를 구하는지 검사한다.
def smoke_test():
    # 250kS/s 설정; 50kHz 신호를 Nyquist 범위 안에서 생성한다.
    sr = 250000
    # 0.2초 길이 시간축을 만든다.
    t = np.arange(sr // 5)/sr
    # 합성 50kHz 순수 사인파(실제 mouse USV보다 매우 단순).
    sine = np.sin(2*np.pi*50000*t)
    # 채널 A의 진폭을 더 크게 만든다.
    a = AudioData("A", None, sr, 0.8*sine)
    # 채널 B는 채널 A보다 상대 진폭이 작다.
    b = AudioData("B", None, sr, 0.2*sine)
    rows = ratio_only_assignment([a,b], [USVEvent(0.02,0.18)])
    # 예상 결과 A가 아니면 AssertionError를 발생시킨다.
    assert rows[0].channel_id == "A", rows
    return rows


def calibration_smoke_test():
    """No GUI required: verify gain/response alter the feature, not raw WAV."""
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from audio.aud_process import (
        calibration_band_level_db, load_frequency_response_csv,
        event_level, window_spectrogram
    )

    sr = 250_000
    t = np.arange(int(sr*.20), dtype=np.float64) / sr
    tone = np.sin(2*np.pi*50_000*t).astype(np.float32)
    ref = AudioData("ch1", None, sr, 0.20*tone)
    mic = AudioData("ch2", None, sr, 0.10*tone)
    before = event_level(mic, USVEvent(.020,.180, 45_000,55_000))
    original_samples = mic.samples.copy()
    ref_level = calibration_band_level_db(ref,.01,.19,40_000,60_000)
    mic_level = calibration_band_level_db(mic,.01,.19,40_000,60_000)
    mic.gain_db = ref_level-mic_level
    mic.apply_gain = True
    after_gain = event_level(mic, USVEvent(.020,.180,45_000,55_000))
    ref_event = event_level(ref, USVEvent(.020,.180,45_000,55_000))
    assert abs(mic.gain_db-6.0206) < .1, mic.gain_db
    assert abs(after_gain-ref_event) < .25, (after_gain,ref_event)

    with TemporaryDirectory() as directory:
        path = Path(directory)/"response.csv"
        path.write_text(
            "frequency_hz,response_db\n"
            "40000,-6\n50000,-6\n60000,-6\n",
            encoding="utf-8"
        )
        f, d = load_frequency_response_csv(path)
        mic.apply_gain = False
        mic.response_freq_hz, mic.response_db = f,d
        mic.apply_response = True
        after_response = event_level(
            mic, USVEvent(.020,.180,45_000,55_000)
        )
        assert abs((after_response-before)-6.0) < .3, (
            after_response,before
        )
    assert np.array_equal(mic.samples, original_samples)
    return {
        "relative_gain_db": float(ref_level-mic_level),
        "response_delta_db": float(after_response-before),
        "raw_wav_unchanged": True
    }


def realtime_media_playback_regression_test():
    """250kS/s source 2s must yield 48kHz preview 2s, NOT 10.42s.

    Tests the same audio exporter used by GUI without constructing a QMainWindow.
    The actual QMediaPlayer backend and moving Qt sliders require a Windows
    integration test after installing the revised ui/controller.py locally.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace
    import wave
    from audio.data_load import AudioData
    from ui.controller import MainWindow

    src_sr = 250_000
    original_s = 2.0
    time = np.arange(round(src_sr * original_s)) / src_sr
    ultrasound = (0.15 * np.sin(2*np.pi*72_000*time)).astype(np.float32)
    source = AudioData("ch1", None, src_sr, ultrasound)

    with TemporaryDirectory() as tmp:
        mock = SimpleNamespace(
            _audio_preview_tmpdir=SimpleNamespace(name=tmp),
            _audio_preview_file_counter=0,
        )
        path, start_s, end_s, time_scale = MainWindow._prepare_audio_preview(
            mock, source, 0.0
        )
        with wave.open(str(path), "rb") as reader:
            frames = reader.getnframes()
            rate = reader.getframerate()
        assert rate == 48000
        assert abs(frames / rate - original_s) < 1.0 / rate
        assert np.isclose(start_s, 0.0)
        assert np.isclose(end_s, original_s)
        assert time_scale == 1.0
    return True


def channel_label_csv_regression_test():
    """Header/preamble, time units, waveform CSV, N-label dedup 회귀 테스트.

    실제 PySide6 GUI가 아니라 로더/이벤트 통합 부분의 자동 검증이다.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from audio.data_load import (
        load_labels, load_audio, inspect_delimited_file
    )
    from audio.voc_label import merge_channel_events

    with TemporaryDirectory() as directory:
        root = Path(directory)
        # Metadata/preamble 위에 실제 onset/offset CSV 헤더가 존재하는 경우.
        ch1 = root / "ch1_label.csv"
        ch1.write_text(
            "Recording exported from a detector\n"
            "Channel 1 labels\n"
            "Start ms,End ms,Low kHz,High kHz\n"
            "100,150,35,72\n300,345,39,77\n",
            encoding="utf-8"
        )
        spec1 = inspect_delimited_file(ch1)
        assert spec1["header_row"] == 2, spec1
        spec1.update({
            "onset_col": 0, "offset_col": 1,
            "fmin_col": 2, "fmax_col": 3,
            "time_unit": "ms", "frequency_unit": "kHz"
        })
        events1 = load_labels(ch1, table_spec=spec1)
        assert len(events1) == 2
        assert np.isclose(events1[0].onset_s, 0.10)
        assert np.isclose(events1[0].fmin_hz, 35000.0)

        ch2 = root / "ch2_label.csv"
        ch2.write_text(
            "onset_s,offset_s,fmin_hz,fmax_hz\n"
            "0.101,0.149,35000,72000\n"
            "0.55,0.59,41000,71000\n",
            encoding="utf-8"
        )
        spec2 = {
            "header_row": 0, "delimiter": ",", "encoding": "utf-8-sig",
            "onset_col": 0, "offset_col": 1,
            "fmin_col": 2, "fmax_col": 3,
            "time_unit": "s", "frequency_unit": "Hz"
        }
        events2 = load_labels(ch2, table_spec=spec2)
        # First event is a duplicate across channels; second is unique to Ch2.
        merged = merge_channel_events([events1, events2])
        assert len(merged) == 3, merged
        assert np.isclose(merged[0].onset_s, 0.1005)
        assert np.isclose(merged[-1].onset_s, 0.55)

        # A legacy STFT label CSV uses frame indices; sr/hop determine times.
        frames = root / "frames.csv"
        frames.write_text("first,last\n100,110\n", encoding="utf-8")
        cfg = {"header_row": 0, "delimiter": ",", "encoding": "utf-8-sig",
               "onset_col": 0, "offset_col": 1,
               "time_unit": "stft_frames", "label_hop_length": 500}
        event = load_labels(
            frames, table_spec=cfg, sr=250000, hop_length=800
        )[0]
        assert np.isclose(event.onset_s, 0.2)
        assert np.isclose(event.offset_s, 0.22)

        # A header that starts after metadata also works for waveform CSV.
        waveform = root / "waveform.csv"
        waveform.write_text(
            "DAQ capture\n"
            "time_s;signal_v\n"
            "0.000;0.2\n0.001;-0.3\n",
            encoding="utf-8"
        )
        audio = load_audio(
            waveform, "ch1", csv_sr=1000,
            csv_spec={"header_row": 1, "encoding": "utf-8-sig",
                      "delimiter": ";", "amplitude_col": 1}
        )
        assert np.allclose(audio.samples, [0.2, -0.3])
    return True


def narrowband_ratio_regression_test():
    """좁은대역 USV + 광대역 잡음 합성 데이터의 A/B 할당 회귀 테스트.

    기존 75th-percentile-over-all-pixels 특징값은 배경 잡음에 의해
    A/B 차이가 약 0dB로 붕괴했다. 새 특징은 다섯 발성의 채널을
    모두 구별해야 한다. 실제 W6 원본 WAV를 검증한 테스트는 아니다.
    """
    sr = 250_000
    rng = np.random.default_rng(202610)
    onsets = [0.10, 0.33, 0.56, 0.79, 1.02]
    expected = ["A", "B", "A", "B", "A"]
    duration = 1.3
    count = int(duration * sr)
    channel_a = rng.normal(0, 0.003, count)
    channel_b = rng.normal(0, 0.003, count)

    width = 0.055
    n_samples = round(width * sr)
    local_t = np.arange(n_samples) / sr
    tone = np.sin(2 * np.pi * 52000 * local_t) * np.hanning(n_samples)
    for onset, owner in zip(onsets, expected):
        first = round(onset * sr)
        channel_a[first:first+n_samples] += (
            0.18 if owner == "A" else 0.035
        ) * tone
        channel_b[first:first+n_samples] += (
            0.18 if owner == "B" else 0.035
        ) * tone

    audio = [
        AudioData("A", None, sr, channel_a),
        AudioData("B", None, sr, channel_b),
    ]
    events = [USVEvent(x, x + width) for x in onsets]
    predictions = ratio_only_assignment(
        audio, events, n_fft=2048, hop_length=512, min_margin_db=3.0
    )
    assignments = [row.channel_id for row in predictions]
    assert assignments == expected, [
        (row.channel_id, row.margin_db, row.levels_db, row.reason)
        for row in predictions
    ]
    assert all(row.margin_db > 8.0 for row in predictions)
    return [(row.channel_id, round(row.margin_db, 2))
            for row in predictions]


def sync_start_smoke_test():
    """같은 사건이 Audio 12.5초 / Video 10초에 있는 경우의 시간 정렬 검증."""
    from .aud_vid_sync import sync_from_start_points
    sync = sync_from_start_points(audio_start_s=12.5, video_start_s=10.0)
    assert abs(sync.offset_s - 2.5) < 1e-9
    assert abs(sync.video_to_audio(15.0) - 17.5) < 1e-9
    assert abs(sync.audio_to_video(17.5) - 15.0) < 1e-9
    return sync


def geometry_formula_smoke_test():
    """문서의 2D 타원·거리·방향 가중치와 두 상충된 모델을 검증한다."""
    from .ratio_corr import (
        EmpiricalGeoConfig, empirical_geometry_weights,
        empirical_distance_weights,
        ellipse_ray_length, empirical_correct_ratio,
        EmpiricalThreshold, empirical_decision
    )
    cfg = EmpiricalGeoConfig(
        body_length_m=0.10, body_width_m=0.04,
        beta=1.0, alpha_theta=0.5, omega_min=0.2
    )
    assert np.isclose(ellipse_ray_length(0.0, cfg), cfg.body_length_m)
    assert np.isclose(ellipse_ray_length(np.pi/2, cfg), 0., atol=1e-9)
    A, B = np.array([0., 0.]), np.array([0.1, 0.])
    ua, ub = np.array([1., 0.]), np.array([-1., 0.])
    w = empirical_geometry_weights(A, B, ua, ub, cfg)
    assert 0. < w["omega_d"] < 1.
    assert 0. < w["omega_theta"] <= 1.
    assert np.isclose(w["omega_geo"], w["omega_d"] * w["omega_theta"])
    corrected, _ = empirical_correct_ratio(-20., -30., A, B, ua, ub, cfg)
    assert corrected > 0.
    # Distance-only does NOT need nose orientation/torso coordinates at all.
    wd = empirical_distance_weights(A, B, cfg)
    corrected_d, from_ratio = empirical_correct_ratio(
        -20., -30., A, B, None, None, cfg, distance_only=True
    )
    assert np.isclose(wd["omega_d"], 1-np.exp(-0.1/(cfg.beta*cfg.body_width_m)))
    assert np.isnan(wd["theta_a_rad"])
    assert wd["omega_theta"] == 1.0
    assert np.isclose(corrected_d, np.log(10)/2/wd["omega_safe"])
    assert np.isclose(from_ratio["omega_geo"], wd["omega_d"])
    cfg_complement = EmpiricalGeoConfig(
        0.10, 0.04, geometry_weight_mode="complement"
    )
    wc = empirical_geometry_weights(A, B, ua, ub, cfg_complement)
    assert np.isclose(wc["omega_geo"], 1. - wc["omega_d"] * wc["omega_theta"])
    # Complement mode must NOT affect the independently defined distance-only weight.
    dc = empirical_distance_weights(A, B, cfg_complement)
    assert np.isclose(dc["omega_geo"], dc["omega_d"])
    # Legacy fitted decision still works for earlier scripts.
    th = EmpiricalThreshold(r_mid=0., mad=0.5, k=1.)
    assert empirical_decision(0.6, th, "A", "B") == "A"
    assert empirical_decision(-0.6, th, "A", "B") == "B"
    assert empirical_decision(0., th, "A", "B") is None

    # No training data needed: manual zero-centered log-amplitude ratio.
    from .ratio_corr import manual_empirical_threshold
    for margin in (0.03, 0.5, 1.0):
        manual = manual_empirical_threshold(margin)
        assert manual.r_mid == 0.0
        assert np.isclose(manual.half_width, margin)
        assert empirical_decision(+margin, manual, "A", "B") is None
        assert empirical_decision(-margin, manual, "A", "B") is None
        assert empirical_decision(margin + .001, manual, "A", "B") == "A"
        assert empirical_decision(-margin - .001, manual, "A", "B") == "B"
    assert empirical_decision(0, manual_empirical_threshold(0), "A", "B") is None
    return w


def audio_video_subject_mapping_smoke_test():
    """A/B audio channel-to-track matching must follow explicit user choices."""
    from video.track_anchor import (
        save_anchor_mapping, validate_audio_video_mapping
    )
    channel_subjects = {"ch1": "mouse_A", "ch2": "mouse_B"}
    mapping = save_anchor_mapping(
        {"sleap_track_17": "mouse_B", "sleap_track_04": "mouse_A"},
        {"mouse_A": "ch1", "mouse_B": "ch2"}
    )
    assert validate_audio_video_mapping(mapping, channel_subjects)
    assert mapping["track_to_mouse"]["sleap_track_17"] == "mouse_B"

    # Reject stale or missing channel associations; order cannot stand in for ID.
    for broken in (
        {"track_to_mouse": mapping["track_to_mouse"]},  # legacy unconfirmed
        {"track_to_mouse": mapping["track_to_mouse"],
         "mouse_to_channel": {"mouse_A": "ch2", "mouse_B": "ch1"}},
    ):
        try:
            validate_audio_video_mapping(broken, channel_subjects)
        except ValueError:
            pass
        else:
            raise AssertionError("Incorrect mapping was not rejected.")
    return True


def label_frequency_smoke_test():
    """CSV 라벨의 주파수 단위, 초 단위를 입력 로더가 보존하는지 검사."""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from audio.data_load import load_labels
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "labels.csv"
        path.write_text(
            "onset,offset,fmin_khz,fmax_khz\n0.1,0.2,35,76\n", encoding="utf-8"
        )
        events = load_labels(path)
        assert len(events) == 1
        assert events[0].fmin_hz == 35000.0
        assert events[0].fmax_hz == 76000.0
    return True


def result_export_smoke_test():
    """Ratio-only 채널 매핑과 geometry 기각을 CSV에서 혼동하지 않는지 확인."""
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from .voc_assign import AssignmentResult
    from executor.result_viewer import result_records, export_results_csv
    event = USVEvent(0.02, 0.18, 35000.0, 70000.0)
    args = {"event": event, "channel_id": "ch1", "margin_db": 6.0,
            "levels_db": {"ch1": -12., "ch2": -18.}, "reason": "test"}
    baseline = AssignmentResult(**args, method="ratio-only")
    rejected = AssignmentResult(**args, method="empirical-unassigned")
    subjects = {"ch1": "mouse_A", "ch2": "mouse_B"}
    rows = list(result_records([baseline, rejected], subjects))
    assert rows[0]["subject_id"] == "mouse_A"
    assert rows[1]["subject_id"] == "unassigned"
    assert rows[0]["fmin_hz"] == 35000.0
    with TemporaryDirectory() as directory:
        output = Path(directory) / "results.csv"
        export_results_csv(output, {"ratio": [baseline],
                                    "geometry": [rejected]}, subjects)
        content = output.read_text(encoding="utf-8-sig")
        assert "analysis_mode" in content
        assert "unassigned" in content
    return True


if __name__ == "__main__":
    print(smoke_test())
    print(narrowband_ratio_regression_test())
    print(channel_label_csv_regression_test())
    print(realtime_media_playback_regression_test())
    print(sync_start_smoke_test())
    print(geometry_formula_smoke_test())
    print(audio_video_subject_mapping_smoke_test())
    print(label_frequency_smoke_test())
    print(result_export_smoke_test())