# [video/track_anchor.py] 영상 track -> 실제 mouse 식별
# track_id는 영상에서 자동 생성되는 임시 ID, mouse_id는 실제 실험체 ID.
# 마이크 array에서는 mouse_id와 channel_id의 1:1 관계가 없을 수도 있다.
# 마우스↔웨어러블 채널 관계가 있을 때만 mouse_to_channel을 선택적으로 저장.
# 실제 영상 프레임을 확인한 뒤 UI에서 사용자가 매핑하도록 한다.
import cv2
import numpy as np
import pandas as pd


def select_anchor_frame(tracking_df, search_first_n_frames=300, expected_mice=None):
    """개체들이 잘 구분되는 초기 프레임을 우선 선택(휴리스틱)."""
    required = {"frame_idx", "track_id", "center_x", "center_y"}
    if not required.issubset(tracking_df.columns):
        raise ValueError("Anchor requires frame_idx, track_id, center_x/y columns.")
    df = tracking_df.dropna(subset=["frame_idx", "track_id", "center_x", "center_y"])
    if df.empty:
        raise ValueError("No track observations available for anchor.")
    first = int(df.frame_idx.min())
    df = df[(df.frame_idx >= first) & (df.frame_idx < first + search_first_n_frames)]
    best_frame, best_score = None, (-1, -1.0)
    for frame, group in df.groupby("frame_idx"):
        group = group.drop_duplicates("track_id")
        if expected_mice is not None and len(group) != expected_mice:
            continue
        coords = group[["center_x", "center_y"]].to_numpy(dtype=float)
        distances = [float(np.linalg.norm(coords[i] - coords[j]))
                     for i in range(len(coords)) for j in range(i+1, len(coords))]
        separation = min(distances) if distances else 0.0
        score = (len(coords), separation)
        if score > best_score:
            best_frame, best_score = int(frame), score
    if best_frame is None:
        raise ValueError("No suitable anchor frame found.")
    return best_frame


def draw_anchor_frame(video_path, tracking_df, frame_index):
    """프레임 위에 추적된 track_id, nose/center 등을 그린 BGR 이미지 반환."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Cannot open video for anchor preview.")
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        okay, frame = capture.read()
    finally:
        capture.release()
    if not okay:
        raise RuntimeError("Failed to decode anchor frame.")
    rows = tracking_df[tracking_df.frame_idx == frame_index]
    for _, row in rows.iterrows():
        if pd.isna(row.get("track_id")) or pd.isna(row.get("center_x")):
            continue
        x, y = int(row.center_x), int(row.center_y)
        cv2.circle(frame, (x, y), 6, (0, 255, 0), -1)
        cv2.putText(frame, str(row.track_id), (x+10, y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        if "nose_x" in rows and pd.notna(row.get("nose_x")):
            cv2.circle(frame, (int(row.nose_x), int(row.nose_y)),
                       4, (0, 255, 255), -1)
    return frame


def save_anchor_mapping(track_to_mouse: dict, mouse_to_channel: dict | None = None):
    """매핑 유효성 검사; 마이크-array에서는 두 번째 인자를 생략 가능."""
    if not track_to_mouse or any(not str(k).strip() or not str(v).strip()
                                for k, v in track_to_mouse.items()):
        raise ValueError("All track IDs must map to a nonblank mouse ID.")
    if len(set(track_to_mouse.values())) != len(track_to_mouse):
        raise ValueError("Multiple tracks mapped to one mouse; review fragmentation.")
    mapping = {"track_to_mouse": dict(track_to_mouse)}
    if mouse_to_channel is not None:
        subjects = set(track_to_mouse.values())
        if set(mouse_to_channel) != subjects:
            raise ValueError("Each mapped video subject needs exactly one audio channel.")
        channels = list(mouse_to_channel.values())
        if (any(not str(cid).strip() for cid in channels)
            or len(set(channels)) != len(channels)):
            raise ValueError("Two mouse subjects cannot map to the same audio channel.")
        mapping["mouse_to_channel"] = dict(mouse_to_channel)
    return mapping


def validate_audio_video_mapping(mapping: dict, channel_subjects: dict):
    """Explicit 1-to-1 association for wearable/per-subject audio channels.

    channel_subjects: {"ch1": "mouse_A", "ch2": "mouse_B"}
    mapping: {"track_to_mouse": {"track_0": "mouse_B", ...},
              "mouse_to_channel": {"mouse_A": "ch1", ...}}
    Track IDs are SLEAP-generated labels, never inferred from audio file order.
    This one-to-one mapping is NOT appropriate for a shared microphone array.
    """
    if not isinstance(mapping, dict):
        raise ValueError("Review Audio ↔ Video track matching before analysis.")
    expected = {subject: ch for ch, subject in channel_subjects.items()}
    if len(expected) != len(channel_subjects):
        raise ValueError("Audio Subject IDs must be unique.")
    tracks = mapping.get("track_to_mouse") or {}
    channels = mapping.get("mouse_to_channel") or {}
    if not tracks or set(tracks.values()) != set(expected):
        raise ValueError(
            "Video tracks must map one-to-one to the current Audio Subject IDs."
        )
    if channels != expected:
        raise ValueError(
            "Audio ↔ Video association is missing or out of date. "
            "Use 'Match Audio ↔ Video subjects' and confirm each channel/track."
        )
    if len(set(tracks.values())) != len(tracks):
        raise ValueError("Video-to-mouse mapping contains duplicate Subject IDs.")
    return True
