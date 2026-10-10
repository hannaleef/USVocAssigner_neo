import cv2
import numpy as np
import pandas as pd
import tkinter as tk

from tkinter import ttk
from tkinter import messagebox


# =========================================================
# Select a good anchor frame
# =========================================================

def select_anchor_frame(
  tracking_df,
  num_mic, 
  track_col="track_id",
  search_first_n_frames=300
):

  required_columns = {"frame_idx", track_col, "center_x", "center_y"}

  missing_columns = (required_columns - set(tracking_df.columns))

  if missing_columns:
    raise ValueError(
      "Missing required columns: "
      + ", ".join(sorted(missing_columns))
  )


  # -----------------------------------------------------
  # 너무 긴 영상 전체를 볼 필요 없이
  # 앞부분 일정 frame만 anchor 후보로 사용
  # -----------------------------------------------------

  first_frame = int(tracking_df["frame_idx"].min())

  last_search_frame = (first_frame + search_first_n_frames)

  search_df = tracking_df[tracking_df["frame_idx"] <= last_search_frame]

  best_frame = None
  best_score = -np.inf

  # -----------------------------------------------------
  # frame별로 확인
  # -----------------------------------------------------

  for frame_idx, frame_data in search_df.groupby("frame_idx"):

		frame_data = frame_data.dropna(
		  subset=[track_col, "center_x", "center_y"]
		)
		
		# 같은 track이 혹시 중복되면 하나만 사용
		frame_data = frame_data.drop_duplicates(subset=[track_col])
		
		# 원하는 개체 수가 모두 보여야 anchor 후보
		if len(frame_data) != num_mic : continue
		
		coordinates = frame_data[["center_x", "center_y"]].to_numpy()
		
		  # -------------------------------------------------
		  # 모든 mouse pair 사이의 거리 계산
		  # -------------------------------------------------
		
		  distances = []
		
		  for i in range(len(coordinates)):
		    for j in range(i + 1,len(coordinates)):
		      distance = np.linalg.norm(coordinates[i] - coordinates[j])
		      distances.append(distance)
		
		  if len(distances) == 0 : continue

    # -------------------------------------------------
    # mouse들이 서로 가장 잘 분리되어 있는 frame 선택
    #
    # 여러 mouse인 경우에도
    # 가장 가까운 pair의 거리가 큰 frame을 선택
    # -------------------------------------------------

    frame_score = min(distances)

    if frame_score > best_score:
      best_score = frame_score
      best_frame = int(frame_idx)

  if best_frame is None:
    raise RuntimeError("Could not find a suitable anchor frame.")

  print(f"Selected anchor frame: {best_frame}")

  print(f"Anchor separation score: {best_score:.2f} pixels")

  return best_frame


# =========================================================
# Draw tracking information on anchor frame
# =========================================================

def draw_anchor_frame(
  video_path,
  tracking_df,
  anchor_frame,
  track_col="track_id"
):

  cap = cv2.VideoCapture(str(video_path))

  if not cap.isOpened():

    raise RuntimeError(f"Could not open video: {video_path}")

  # 원하는 frame으로 이동
  cap.set(
    cv2.CAP_PROP_POS_FRAMES,
    anchor_frame
  )

  success, frame = cap.read()

  cap.release()

  if not success:
    raise RuntimeError(f"Could not read frame {anchor_frame}.")

  # -----------------------------------------------------
  # 해당 frame의 tracking data
  # -----------------------------------------------------

  frame_data = tracking_df[
    tracking_df["frame_idx"] == anchor_frame
  ]

  # -----------------------------------------------------
  # 각 track 표시
  # -----------------------------------------------------

  for _, row in frame_data.iterrows():

    if pd.isna(row.get(track_col)):
        continue

    if (pd.isna(row["center_x"]) or
        pd.isna(row["center_y"])) : continue

    x = int(row["center_x"])
    y = int(row["center_y"])

    track_id = str(row[track_col])

    # center point
    cv2.circle(frame, (x, y), 8, (0, 255, 0), -1)

    # track ID
    cv2.putText(
      frame,
      track_id,
      (x + 10, y - 10),
      cv2.FONT_HERSHEY_SIMPLEX,
      0.8,
      (0, 255, 0),
      2,
      cv2.LINE_AA
    )

    # -------------------------------------------------
    # SLEAP body points도 함께 표시
    # -------------------------------------------------

    body_points = [
      ("nose_x", "nose_y"),
      ("left_ear_x", "left_ear_y"),
      ("right_ear_x","right_ear_y"),
      ("torso_x", "torso_y"),
      ("tail_base_x", "tail_base_y")
    ]

    for x_col, y_col in body_points:

      if (
          x_col not in tracking_df.columns
          or
          y_col not in tracking_df.columns
      ):
        continue

      point_x = row[x_col]
      point_y = row[y_col]

      if (pd.isna(point_x) or pd.isna(point_y)) :
        continue

      cv2.circle(
        frame,
        (int(point_x),int(point_y)),
        3,
        (255, 255, 255),
        -1
      )

  # frame 번호도 표시
  cv2.putText(
    frame,
    f"Anchor frame: {anchor_frame}",
    (20, 40),
    cv2.FONT_HERSHEY_SIMPLEX,
    1.0,
    (0, 255, 255),
    2,
    cv2.LINE_AA
  )

  return frame


# =========================================================
# Show anchor frame
# =========================================================

def show_anchor_frame(frame, window_name="Anchor Frame") :

  display_frame = frame.copy()

  # 너무 큰 영상이면 화면에 맞게 축소
  height, width = display_frame.shape[:2]

  max_width = 1200
  max_height = 800

  scale = min(
      max_width / width,
      max_height / height,
      1.0
  )

  if scale < 1.0:
    display_frame = cv2.resize(
        display_frame,
        None,
        fx=scale,
        fy=scale
    )

  cv2.imshow(window_name, display_frame)

  print(
      "Press any key on the image window "
      "after checking the track IDs."
  )

  cv2.waitKey(0)
  cv2.destroyAllWindows()


  # =========================================================
# Ask user to match:
#
# track_id -> real mouse -> audio channel
# =========================================================

def ask_anchor_mapping(track_ids, mouse_ids):

  result = None
  root = tk.Tk()
  root.title("Track Anchor")
  root.geometry("500x300")

  tk.Label(
      root,
      text=(
          "Match each video track "
          "to the real mouse and audio channel."
      ),
      font=("Arial", 12,"bold")
  ).pack(pady=15)

  frame = tk.Frame(root)
  frame.pack(padx=20, pady=10)

  # -----------------------------------------------------
  # Header
  # -----------------------------------------------------

  tk.Label(
      frame,
      text="Video Track",
      width=15
  ).grid(row=0, column=0)

  tk.Label(
      frame,
      text="Mouse ID",
      width=15
  ).grid(row=0, column=1)

  mouse_boxes = {}

  # -----------------------------------------------------
  # One row per track
  # -----------------------------------------------------

  for row_idx, track_id in enumerate(track_ids, start=1):

    tk.Label(
        frame,
        text=str(track_id)
    ).grid(
        row=row_idx,
        column=0,
        padx=5,
        pady=5
    )

    mouse_box = ttk.Combobox(
        frame,
        values=mouse_ids,
        state="readonly",
        width=12
    )

    mouse_box.grid(
        row=row_idx,
        column=1,
        padx=5
    )

    mouse_boxes[track_id] = mouse_box



# -----------------------------------------------------
# Confirm button
# -----------------------------------------------------

def confirm():

  nonlocal result

  track_to_mouse = {}
  selected_mice = []

  for track_id in track_ids:

    mouse_id = mouse_boxes[track_id].get()

    if (mouse_id == ""):
        messagebox.showerror(
            "Anchor Error",
            "Please select both mouse ID "
            "for every track."
        )
        return

    track_to_mouse[track_id] = mouse_id

    selected_mice.append(mouse_id)

  # -------------------------------------------------
  # 같은 mouse를 두 track에 지정하는 실수 방지
  # -------------------------------------------------

  if (len(set(selected_mice)) != len(selected_mice)):
    messagebox.showerror(
      "Anchor Error",
      "The same mouse cannot be "
      "assigned to multiple tracks."
    )
    return

  result = {"track_to_mouse": track_to_mouse}

  root.destroy()

	confirm_button = tk.Button(
    root,
    text="Confirm",
    command=confirm,
    width=15
	)
	
	confirm_button.pack(pady=20)
	root.mainloop()
	
	return result


# =========================================================
# Main interface
# =========================================================

def anchor_tracks(
  video_path,
  tracking_df,
  mouse_ids,
  track_col="track_id",
  expected_num_tracks=2,
  search_first_n_frames=300
):

  # -----------------------------------------------------
  # 1. Select anchor frame
  # -----------------------------------------------------

  anchor_frame = select_anchor_frame(
    tracking_df,
    track_col=track_col,
    expected_num_tracks=expected_num_tracks,
    search_first_n_frames=search_first_n_frames
  )

  # -----------------------------------------------------
  # 2. Draw tracking result
  # -----------------------------------------------------

  frame = draw_anchor_frame(
    video_path,
    tracking_df,
    anchor_frame,
    track_col=track_col
  )

  # -----------------------------------------------------
  # 3. Show image
  # -----------------------------------------------------

  show_anchor_frame(frame)

  # -----------------------------------------------------
  # 4. Find tracks that exist at anchor frame
  # -----------------------------------------------------

  anchor_data = tracking_df[
    tracking_df["frame_idx"]
    == anchor_frame
  ]

  track_ids = (
    anchor_data[track_col]
      .dropna()
      .unique()
      .tolist()
  )

  # -----------------------------------------------------
  # 5. Ask user for identity mapping
  # -----------------------------------------------------

  mapping = ask_anchor_mapping(
    track_ids,
    mouse_ids
  )

  if mapping is None:
    print("Anchor mapping canceled.")
    return None

  # anchor frame 정보도 같이 저장
  mapping["anchor_frame"] = anchor_frame

  return mapping
