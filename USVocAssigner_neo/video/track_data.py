# [video/track_data.py] 영상 추적 CSV -> 개체별 시간·좌표 테이블
# 우선순위: SLEAP이 제공한 track ID를 보존한다. ID가 없는 CSV만 Hungarian 처리.
# Hungarian은 프레임당 거리 최소 매칭이며 교차·가림에서 ID swap이 가능하다.
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment


def prepare_tracks(raw_df: pd.DataFrame, fps: float) -> pd.DataFrame:
    """SLEAP columns를 표준화하고 frame_idx -> time_video_s[초] 생성."""
    if fps <= 0:
        raise ValueError("Video FPS must be positive.")
    df = raw_df.rename(columns=lambda col: str(col).replace(".", "_")).copy()
    if "frame_idx" not in df.columns:
        raise ValueError("Tracking CSV has no frame_idx column.")
    df["frame_idx"] = pd.to_numeric(df["frame_idx"], errors="coerce")
    df = df.dropna(subset=["frame_idx"])
    df["frame_idx"] = df["frame_idx"].astype(int)
    df["time_video_s"] = df["frame_idx"] / float(fps)

    x_cols = [col for col in ("nose_x", "left_ear_x", "right_ear_x",
                              "torso_x", "tail_base_x") if col in df]
    y_cols = [col for col in ("nose_y", "left_ear_y", "right_ear_y",
                              "torso_y", "tail_base_y") if col in df]
    if not x_cols or not y_cols:
        raise ValueError("Required mouse body point coordinates not found.")
    for col in set(x_cols + y_cols):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["center_x"] = df[x_cols].mean(axis=1)
    df["center_y"] = df[y_cols].mean(axis=1)

    # SLEAP 추적 결과가 'track' 열을 제공하면 자체 track_id에 복사하여 보존.
    # 기존 track_id 열이 이미 있다면 그것을 우선 사용한다.
    if "track_id" not in df.columns and "track" in df.columns:
        df["track_id"] = df["track"]
    if "track_id" in df.columns:
        df["track_id"] = df["track_id"].apply(
            lambda value: str(value) if pd.notna(value)
            and str(value).strip() not in ("", "nan", "None") else None
        )
    return df


def identify_tracks(df: pd.DataFrame, max_distance_px=65.0,
                    max_missing_frames=5) -> pd.DataFrame:
    """SLEAP track_id가 없는 데이터에 Hungarian 기반 임시 track_id 부여."""
    if df.empty:
        raise ValueError("Tracking table is empty.")
    if max_distance_px <= 0 or max_missing_frames < 0:
        raise ValueError("Invalid distance/missing-frames limits.")
    out = df.copy()
    out["track_id"] = None
    # track 상태: [x, y, last_frame, miss]
    active = {}
    next_id = 0
    frame_start, frame_stop = int(out.frame_idx.min()), int(out.frame_idx.max())

    for frame in range(frame_start, frame_stop + 1):
        current = out[(out.frame_idx == frame) & out.center_x.notna()
                      & out.center_y.notna()]
        indices, keys = list(current.index), list(active)
        costs = np.full((len(keys), len(indices)), 1e9, dtype=float)

        for i, key in enumerate(keys):
            x, y, last_frame, _ = active[key]
            for j, idx in enumerate(indices):
                obs = current.loc[idx]
                costs[i, j] = np.hypot(x - obs.center_x, y - obs.center_y)
        matched_keys, matched_indices = set(), set()

        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols):
                if costs[i, j] > max_distance_px:
                    continue  # 운동 범위를 벗어난 연결은 기각.
                key, idx = keys[i], indices[j]
                obs = current.loc[idx]
                active[key] = [obs.center_x, obs.center_y, frame, 0]
                out.at[idx, "track_id"] = key
                matched_keys.add(key)
                matched_indices.add(idx)

        for key in keys:
            if key not in matched_keys:
                active[key][3] += 1
        active = {key: val for key, val in active.items()
                  if val[3] <= max_missing_frames}

        for idx in indices:
            if idx in matched_indices:
                continue
            key = f"track_{next_id}"
            next_id += 1
            obs = current.loc[idx]
            active[key] = [obs.center_x, obs.center_y, frame, 0]
            out.at[idx, "track_id"] = key
    return out


def process_tracking_data(raw_df, fps, max_distance_px=65.0):
    """GUI가 호출하는 단일 진입점: SLEAP 기본 track 우선, 없으면 자체 추적."""
    df = prepare_tracks(raw_df, fps)
    if "track_id" not in df or df["track_id"].isna().all():
        return identify_tracks(df, max_distance_px=max_distance_px)
    # 일부 검출에서 track_id가 누락되면 임의 ID 매핑을 섞지 않고 누락으로 보존.
    # 실제 발성 개체 판정 단계에서는 track_id가 없는 행은 사용하지 않는다.
    return df.sort_values(["frame_idx"]).reset_index(drop=True)
