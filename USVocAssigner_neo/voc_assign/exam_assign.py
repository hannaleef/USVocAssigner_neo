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
    cfg_complement = EmpiricalGeoConfig(
        0.10, 0.04, geometry_weight_mode="complement"
    )
    wc = empirical_geometry_weights(A, B, ua, ub, cfg_complement)
    assert np.isclose(wc["omega_geo"], 1. - wc["omega_d"] * wc["omega_theta"])
    th = EmpiricalThreshold(r_mid=0., mad=0.5, k=1.)
    assert empirical_decision(0.6, th, "A", "B") == "A"
    assert empirical_decision(-0.6, th, "A", "B") == "B"
    assert empirical_decision(0., th, "A", "B") is None
    return w


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
    print(sync_start_smoke_test())
    print(geometry_formula_smoke_test())
    print(label_frequency_smoke_test())
    print(result_export_smoke_test())
