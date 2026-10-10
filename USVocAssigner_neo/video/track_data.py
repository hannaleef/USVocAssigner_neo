import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

# =========================================================
# Body-point columns used to calculate mouse center
# =========================================================

CENTER_X_COLUMNS = [
    "nose_x",
    "left_ear_x",
    "right_ear_x",
    "torso_x",
    "tail_base_x"
]

CENTER_Y_COLUMNS = [
    "nose_y",
    "left_ear_y",
    "right_ear_y",
    "torso_y",
    "tail_base_y"
]


# =========================================================
# Calculate mouse center
# =========================================================

def calculate_center(tracking_df):

    tracking_df = tracking_df.copy()

    # 실제 DataFrame에 존재하는 column만 사용
    x_columns = [
        col for col in CENTER_X_COLUMNS
        if col in tracking_df.columns
    ]

    y_columns = [
        col for col in CENTER_Y_COLUMNS
        if col in tracking_df.columns
    ]

    if not x_columns or not y_columns:
        raise ValueError(
            "Could not calculate center coordinates. "
            "No usable body-point coordinate columns were found."
        )

    tracking_df["center_x"] = (
	    tracking_df[x_columns].mean(axis=1,skipna=True)
	    )

    tracking_df["center_y"] = (
	    tracking_df[y_columns].mean(axis=1,skipna=True)
	    )

    return tracking_df


# =========================================================
# Create a new active track
# =========================================================

def create_track(current_track, frame_id)
  return {
    "center_x":
      float(current_track["center_x"]),

    "center_y":
      float(current_track["center_y"]),

    "velocity_x": 0.0,
    "velocity_y": 0.0,

    "last_frame":
      int(frame_idx),

    "missing_frames": 0
  }


# =========================================================
# Calculate distance cost matrix
# =========================================================

def calculate_distance(active_tracks, current_frame, frame_idx):
  # 기존에 추적 중인 mouse ID
  active_ids = list(active_tracks.keys())

  # 현재 frame의 detection들의 DataFrame index
  current_indices = list(current_frame.index)

  # cost matrix 생성
  distance_cost = np.full(
    (len(active_ids),len(current_indices)),
    1e12,
    dtype=float
  )

  # -----------------------------------------
  # each active mouse
  # -----------------------------------------

  for row_idx, track_id in enumerate(active_ids):

    active_track = active_tracks[track_id]

    # 마지막으로 발견된 frame에서
    # 현재 frame까지 몇 frame 지났는지
    frame_gap = max(
      int(frame_idx)
      - active_track["last_frame"],
      1
    )

    # 이전 위치와 속도를 이용하여
    # 현재 frame의 예상 위치 계산
    predicted_x = (
      active_track["center_x"]
      +
      active_track["velocity_x"]
      * frame_gap
    )

    predicted_y = (
      active_track["center_y"]
      +
      active_track["velocity_y"]
      * frame_gap
    )

    # -----------------------------------------
    # each current detection
    # -----------------------------------------

    for col_idx, dataframe_idx in enumerate(current_indices):

      current_track = current_frame.loc[dataframe_idx]
      current_x = current_track["center_x"]
      current_y = current_track["center_y"]

      # center 좌표가 없으면 matching 불가능
      if (
        pd.isna(current_x)
        or pd.isna(current_y)
      ): continue

      # Euclidean distance
      distance = np.hypot(
        current_x - predicted_x,
        current_y - predicted_y
      )

      distance_cost[row_idx, col_idx] = distance

  return (active_ids, current_indices, distance_cost)


# =========================================================
# Hungarian assignment
# =========================================================

def match_tracks(
  active_tracks,
  current_frame,
  frame_idx,
  max_distance
) :
  # 기존 mouse가 하나도 없는 경우
  if len(active_tracks) == 0:
    return ([], [], list(current_frame.index))

  # 현재 frame에 detection이 하나도 없는 경우
  if len(current_frame) == 0:
    return ([], list(active_tracks.keys()), [])

  active_ids, current_indices, distance_cost = calculate_distance(
    active_tracks,
    current_frame,
    frame_idx
  )

  # Hungarian assignment
  match_rows, match_cols = (linear_sum_assignment(distance_cost))

  matches = []

  matched_track_ids = set()
  matched_detection_indices = set()

  # -----------------------------------------
  # Check each Hungarian assignment
  # -----------------------------------------

  for row, col in zip(match_rows, match_cols):

    distance = float(distance_cost[row, col])
    track_id = active_ids[row]

    detection_idx = current_indices[col]

    # Hungarian algorithm이 연결했다고
    # 무조건 같은 mouse로 인정하지 않음
    if distance <= max_distance:
      matches.append(track_id, detection_idx, distance)
      matched_track_ids.add(track_id)
      matched_detection_indices.add(detection_idx)

  # -----------------------------------------
  # 기존 track 중 matching 실패
  # -----------------------------------------

  unmatched_tracks = [
    track_id
    for track_id in active_ids
    if track_id
    not in matched_track_ids
  ]

  # -----------------------------------------
  # 현재 detection 중 matching 실패
  # -----------------------------------------

  unmatched_detections = [
    dataframe_idx
    for dataframe_idx
    in current_indices
    if dataframe_idx
    not in matched_detection_indices
  ]

  return (matches, unmatched_tracks, unmatched_detections)


# =========================================================
# Update active track
# =========================================================

def update_track(active_track, current_track, frame_idx) :

    frame_gap = max(
        int(frame_idx)
        - active_track["last_frame"], 1
    )

    new_x = float( current_track["center_x"])

    new_y = float(current_track["center_y"])

    # -----------------------------------------
    # Calculate velocity
    # -----------------------------------------

    velocity_x = (
        new_x
        - active_track["center_x"]
    ) / frame_gap

    velocity_y = (
        new_y - active_track["center_y"]
    ) / frame_gap

    # -----------------------------------------
    # Update track state
    # -----------------------------------------

    active_track["center_x"] = new_x
    active_track["center_y"] = new_y
    
    active_track["velocity_x"] = velocity_x
    active_track["velocity_y"] = velocity_y

    active_track["last_frame"] = int(frame_idx)

    active_track["missing_frames"] = 0


# =========================================================
# Identify mouse across frames
# =========================================================
	
def identify_tracks(
  tracking_df,
  max_distance,
  max_missing_frames=5
):

    if max_distance <= 0:
      raise ValueError("max_distance must be greater than 0.")

    if max_missing_frames < 0:
      raise ValueError("max_missing_frames must be 0 or greater.")

    # -----------------------------------------
    # Calculate center
    # -----------------------------------------

    tracking_df = calculate_center(tracking_df)

    # 최종 mouse identity
    tracking_df["mouse_id"] = pd.Series(
      index=tracking_df.index,
      dtype="object"
    )

    # matching이 얼마나 떨어져 있었는지 기록
    tracking_df["assignment_distance"] = np.nan


    # -----------------------------------------
    # Find frame range
    # -----------------------------------------

    valid_frames = tracking_df["frame_idx"].dropna()

    if valid_frames.empty:
      return tracking_df

    first_frame = int(valid_frames.min())
    last_frame = int(valid_frames.max())


    # -----------------------------------------
    # Track state
    # -----------------------------------------

    active_tracks = {}
    next_track_number = 0

    # -----------------------------------------
    # Process each frame
    # -----------------------------------------

	  for frame_idx in range(first_frame, last_frame + 1):
	    current_frame = tracking_df[
	      tracking_df["frame_idx"]
	      == frame_idx
	    ]
	    # center 좌표가 없는 detection은 제외
	    current_frame = (
	      current_frame.dropna(
	        subset=["center_x", "center_y"]
	      )
	    )
      # =================================================
      # No existing active tracks
      # =================================================

    if len(active_tracks) == 0:
      for (dataframe_idx, current_track) in current_frame.iterrows():
        track_id = (f"mouse_{next_tracke_number}")
        next_track_number += 1
        active_tracks[track_id] = create_track(
            current_track,
            frame_idx
        )

        tracking_df.at[dataframe_idx, "track_id"] = track_id
        
        # 처음 만들어진 track은
        # 이전 frame과의 distance가 없음
        tracking_df.at[dataframe_idx, "assignment_distance"] = 0.0

      continue

      # =================================================
      # Match active tracks and current detections
      # =================================================

      (matches, unmatched_tracks, unmatched_detections) = match_tracks(
        active_tracks,
        current_frame,
        frame_idx,
        max_distance
      )

      # =================================================
      # Update matched tracks
      # =================================================

      for (track_id, dataframe_idx, distance) in matches:
        current_track = current_frame.loc[dataframe_idx]
        update_track(active_tracks[track_id], current_track, frame_idx)

        tracking_df.at[dataframe_idx, "track_id"] = track_id
        tracking_df.at[dataframe_idx, "assignment_distance"] = distance


      # =================================================
      # Count missing frames
      # =================================================

      for track_id in unmatched_tracks:
          active_tracks[track_id]["missing_frames"] += 1

      # =================================================
      # Remove stale tracks
      # =================================================

      stale_tracks = [
        track_id
        for track_id, track
        in active_tracks.items()
        if (
            track["missing_frames"]
            > max_missing_frames
        )
      ]

      for track_id in stale_tracks:
        del active_tracks[track_id]


      # =================================================
      # Create new ID for unmatched detections
      # =================================================

      for dataframe_idx in (unmatched_detections):
        current_track = current_frame.loc[dataframe_idx]
        track_id = (f"mouse_{next_track_number}")
        next_track_number += 1

        active_tracks[track_id] = create_track(current_track, frame_idx)

        tracking_df.at[dataframe_idx, "track_id"] = track_id

  return tracking_df


# =========================================================
# Main interface of track_data.py
# =========================================================

def process_tracking_data(
  tracking_df,
  max_distance,
  max_missing_frames=5
):

  if tracking_df is None:
    raise ValueError("tracking_df is None.")

  required_columns = {"frame_idx"}
  missing_columns = (required_columns - set(tracking_df.columns))

  if missing_columns:
    raise ValueError(
        "Missing required columns: "
        + ", ".join(sorted(missing_columns))
    )

  processed_df = identify_tracks(
    tracking_df,
    max_distance=max_distance,
    max_missing_frames=max_missing_frames
  )

  return processed_df
