# [vocassign/voc_assign.py] ratio-only baseline 및 영상/거리/각도 최종 판정
# 결과 1개는 USVEvent 1개에 대응한다(채널마다 중복 결과를 만들지 않는다).
# 검증 전 거리/각도 보정은 절대 '정확도 상승'을 보장하지 않는다.
# 유효 좌표, 실측 마이크 위치, pixel-to-meter 보정, tracking anchor가
# 하나라도 없으면 안전하게 ratio-only 결과로 fallback한다.
from dataclasses import dataclass, field, replace
import numpy as np
import pandas as pd

from audio.aud_process import event_level
from .ratio_calc import correct_channel_levels, rank_levels
from .ratio_corr import compare_candidate, GeometryConfig
from .aud_vid_sync import SyncConfig


@dataclass
class AssignmentResult:
    event: object
    channel_id: str | None
    margin_db: float
    levels_db: dict
    reason: str
    mouse_id: str | None = None
    method: str = "ratio-only"
    candidate_errors_db: dict = field(default_factory=dict)


def ratio_only_assignment(audio_dataset, events, min_margin_db=3.0,
                          n_fft=1024, hop_length=256,
                          recording_bias_db=None):
    """채널별 동일 시간 구간의 상대 레벨을 비교하는 기준 알고리즘."""
    if len(audio_dataset) < 2:
        raise ValueError("Need at least two audio channels.")
    if len({a.channel_id for a in audio_dataset}) != len(audio_dataset):
        raise ValueError("Channel IDs must be unique.")
    if len({a.sr for a in audio_dataset}) != 1:
        raise ValueError("Sample rates must match (resampling not implemented).")
    results = []
    for event in events:
        # 모든 채널에서 동일한 시간 범위를 측정한다.
        raw_levels = {a.channel_id: event_level(a, event, n_fft, hop_length)
                      if event.offset_s <= a.duration_s else float("nan")
                      for a in audio_dataset}
        levels = correct_channel_levels(raw_levels, recording_bias_db)
        ranked, margin = rank_levels(levels)
        accepted = bool(len(ranked) >= 2 and margin >= min_margin_db)
        channel = ranked[0][0] if accepted else None
        reason = "largest calibrated relative level" if accepted else "ambiguous/invalid"
        results.append(AssignmentResult(event, channel, margin, levels, reason))
    return results


def _nearest_frame_rows(tracking_df, target_video_s, tolerance_s=0.06):
    """USV 중심 시각과 가장 가까운 비디오 프레임을 반환."""
    if tracking_df is None or tracking_df.empty:
        return None
    times = tracking_df["time_video_s"].dropna()
    if times.empty:
        return None
    nearest = (times - target_video_s).abs().idxmin()
    nearest_frame = int(tracking_df.loc[nearest, "frame_idx"])
    frame_rows = tracking_df[tracking_df.frame_idx == nearest_frame]
    actual_time = float(tracking_df.loc[nearest, "time_video_s"])
    return frame_rows if abs(actual_time - target_video_s) <= tolerance_s else None


def geometry_assignment(baseline_results, tracking_df, anchor_mapping,
                        geometry: GeometryConfig, sync: SyncConfig,
                        max_frame_gap_s=0.06):
    """마이크 배열 관측 패턴과 각 후보 마우스의 거리·방향 모델을 비교."""
    mapping = anchor_mapping.get("track_to_mouse", {})
    results = []
    for baseline in baseline_results:
        event = baseline.event
        midpoint_audio_s = (event.onset_s + event.offset_s) / 2
        video_s = sync.audio_to_video(midpoint_audio_s)
        frame_rows = _nearest_frame_rows(tracking_df, video_s, max_frame_gap_s)

        if frame_rows is None:
            results.append(replace(baseline, reason="no synchronized frame; ratio-only"))
            continue

        candidate_errors = {}
        # SLEAP의 nose와 torso가 유효한 프레임만 후보로 채택한다.
        required = ("nose_x", "nose_y", "torso_x", "torso_y", "track_id")
        if any(col not in frame_rows.columns for col in required):
            results.append(replace(baseline, reason="missing nose/torso points; ratio-only"))
            continue
        for _, row in frame_rows.iterrows():
            track = row["track_id"]
            mouse = mapping.get(str(track))
            if mouse is None or any(pd.isna(row[col]) for col in required):
                continue
            nose = np.array([row["nose_x"], row["nose_y"]], dtype=float)
            torso = np.array([row["torso_x"], row["torso_y"]], dtype=float)
            heading = nose - torso
            try:
                error = compare_candidate(baseline.levels_db, nose, heading, geometry)
            except (ValueError, KeyError):
                continue
            if np.isfinite(error):
                # 동일 mouse_id가 두 track에 있으면 모델이 불분명하므로 점수를 갱신하지 않는다.
                if mouse not in candidate_errors:
                    candidate_errors[mouse] = float(error)

        ranked = sorted(candidate_errors.items(), key=lambda pair: pair[1])
        if len(ranked) < 2:
            results.append(replace(
                baseline, reason="insufficient tracked candidates; ratio-only",
                candidate_errors_db=candidate_errors
            ))
            continue

        best_mouse, best_error = ranked[0]
        gap = ranked[1][1] - best_error  # 낮은 RMSE가 좋고 gap은 큰 것이 좋음.
        is_accepted = (best_error <= geometry.max_error_db
                       and gap >= geometry.min_gap_db)
        if is_accepted:
            results.append(replace(
                baseline, mouse_id=str(best_mouse), method="geometry",
                reason=f"geometry pattern RMSE={best_error:.2f} dB; gap={gap:.2f} dB",
                candidate_errors_db=candidate_errors
            ))
        else:
            # 안전장치: 기하학적 모델이 불확실하면 mouse_id는 None으로 유지.
            results.append(replace(
                baseline, mouse_id=None, method="geometry-unassigned",
                reason=f"geometry rejected: RMSE={best_error:.2f}, gap={gap:.2f} dB",
                candidate_errors_db=candidate_errors
            ))
    return results


def run_assignment(audio_dataset, events, tracking_df=None, anchor_mapping=None,
                   geometry=None, sync=None, n_fft=1024, hop_length=256,
                   min_ratio_margin_db=3.0):
    """일관된 파이프라인: baseline -> (선택적) geometry 최종 판정."""
    bias = geometry.recording_bias_db if geometry is not None else None
    baseline = ratio_only_assignment(
        audio_dataset, events, min_margin_db=min_ratio_margin_db,
        n_fft=n_fft, hop_length=hop_length, recording_bias_db=bias
    )
    if tracking_df is None or anchor_mapping is None or geometry is None:
        return baseline
    return geometry_assignment(
        baseline, tracking_df, anchor_mapping, geometry, sync or SyncConfig()
    )


# ---------------------------------------------------------
# Experiment mode: two subjects only, empirical reliability model
# ---------------------------------------------------------
from .ratio_corr import (
    EmpiricalGeoConfig, EmpiricalThreshold,
    empirical_correct_ratio, empirical_decision, pixel_to_m
)


def _tracked_pair_at_audio(event, tracking_df, anchor_mapping, sync,
                           id_a, id_b, tolerance_s=0.06,
                           require_orientation=True):
    """Find two annotated mice in the synchronized video frame.

    Returns (nose_A, nose_B, forward_A, forward_B) in pixel coordinates.
    SLEAP track_id is NOT assumed to be equal to mouse_id.
    """
    time_video_s = sync.audio_to_video((event.onset_s + event.offset_s)/2)
    current = _nearest_frame_rows(tracking_df, time_video_s, tolerance_s)
    if current is None:
        return None
    track_to_mouse = anchor_mapping.get("track_to_mouse", {})
    positions = {}
    for _, row in current.iterrows():
        mouse = track_to_mouse.get(str(row.get("track_id")))
        if mouse not in (id_a, id_b):
            continue
        # Distance-only는 nose 좌표만 있어도 계산 가능하도록 한다.
        # Geometry 모드에는 torso 좌표를 통한 방향 벡터가 반드시 필요하다.
        required = ("nose_x", "nose_y")
        if require_orientation:
            required += ("torso_x", "torso_y")
        if any(field not in current.columns or pd.isna(row[field]) for field in required):
            continue
        nose = np.array([row.nose_x, row.nose_y], float)
        if require_orientation:
            forward = nose - np.array([row.torso_x, row.torso_y], float)
        else:
            forward = np.array([1.0, 0.0])  # 거리 모드에서 방향값은 사용되지 않음.
        positions[mouse] = (nose, forward)
    if not all(mid in positions for mid in (id_a, id_b)):
        return None
    return positions[id_a][0], positions[id_b][0], positions[id_a][1], positions[id_b][1]


def empirical_ratio_assignment(baseline_results, tracking_df, anchor_mapping,
                               sync: SyncConfig, geometry: GeometryConfig,
                               empirical: EmpiricalGeoConfig,
                               threshold: EmpiricalThreshold,
                               channel_subjects: dict,
                               distance_only=False):
    """Apply the Notion-defined R_amp/omega_geo empirical model.

    Requires two channels with an explicit subject mapping, stable track IDs,
    a measured pixel-to-meter transform, two correct anchors, and a
    threshold learned on training data. No invented threshold is used.
    """
    if len(channel_subjects) != 2:
        raise ValueError("Empirical A/B decision requires exactly TWO channels.")
    if len(set(channel_subjects.values())) != 2:
        raise ValueError("Both channels need distinct real subject IDs.")
    (channel_a, id_a), (channel_b, id_b) = list(channel_subjects.items())
    results = []
    for baseline in baseline_results:
        pair = _tracked_pair_at_audio(
            baseline.event, tracking_df, anchor_mapping, sync, id_a, id_b,
            require_orientation=not distance_only
        )
        if pair is None or not all(
            np.isfinite(baseline.levels_db.get(cid, np.nan))
            for cid in (channel_a, channel_b)
        ):
            results.append(replace(baseline, method="empirical-unassigned",
                                   mouse_id=None,
                                   reason="No synchronized two-mouse frame/level"))
            continue
        nose_a, nose_b, heading_a, heading_b = pair
        try:
            # Transform both ends of the orientation vector; under a
            # homography a vector cannot be scaled directly as pixel->metre.
            H = geometry.pixel_to_meter_h
            p_a = pixel_to_m(nose_a, H)
            p_b = pixel_to_m(nose_b, H)
            u_a = p_a - pixel_to_m(nose_a - heading_a, H)
            u_b = p_b - pixel_to_m(nose_b - heading_b, H)
            r_corr, weights = empirical_correct_ratio(
                baseline.levels_db[channel_a], baseline.levels_db[channel_b],
                p_a, p_b, u_a, u_b, empirical, distance_only
            )
            mouse = empirical_decision(r_corr, threshold, id_a, id_b)
        except (ValueError, ZeroDivisionError, FloatingPointError):
            results.append(replace(baseline, method="empirical-unassigned",
                                   mouse_id=None, reason="invalid geometry"))
            continue
        method = "distance-weight" if distance_only else "geometry-weight"
        results.append(replace(
            baseline, method=method, mouse_id=mouse,
            reason=(f"R_corr={r_corr:+.4f} log-unit; "
                    f"omega={weights['omega_geo']:.3f}; "
                    f"omega_safe={weights['omega_safe']:.3f}; "
                    f"R_mid={threshold.r_mid:+.3f} MAD={threshold.mad:.3f}; "
                    f"{'assigned' if mouse else 'unassigned'}"),
            candidate_errors_db={**baseline.candidate_errors_db,
                                 "R_corr_log": r_corr,
                                 "omega_d": weights["omega_d"],
                                 "omega_theta": weights["omega_theta"],
                                 "omega_geo": weights["omega_geo"]}
        ))
    return results


def run_empirical_assignment(audio_dataset, events, tracking_df, anchor_mapping,
                             geometry, empirical, threshold, channel_subjects,
                             sync=None, distance_only=False,
                             n_fft=1024, hop_length=256):
    """GUI test tab calls this after checking all required measured inputs."""
    channels = {a.channel_id for a in audio_dataset}
    if channels != set(channel_subjects) or len(channels) != 2:
        raise ValueError("Exactly two mapped audio channels are required.")
    if geometry is None or empirical is None or threshold is None:
        raise ValueError("Measured geometry and fitted threshold JSON required.")
    if tracking_df is None or anchor_mapping is None or sync is None:
        raise ValueError("Tracking, anchor IDs and synchronized start points required.")
    baseline = ratio_only_assignment(
        audio_dataset, events, n_fft=n_fft, hop_length=hop_length,
        recording_bias_db=geometry.recording_bias_db
    )
    return empirical_ratio_assignment(
        baseline, tracking_df, anchor_mapping, sync, geometry,
        empirical, threshold, channel_subjects,
        distance_only=distance_only
    )

