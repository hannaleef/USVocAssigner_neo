import subprocess as sp
import pandas as pd
import cv2
import tkinter as tk
from tkinter import filedialog

import data_load as dl

import shutil
from pathlib import Path


SLEAP_VERSION = "1.6.3"
SLEAP_IO_VERSION = "0.7.0"
SLEAP_NN_VERSION = "0.2.0"
PYTHON_VERSION = "3.13"


def find_uv():
  uv = shutil.which("uv")
  
  if uv:
    return Path(uv)
    
  default_uv = (Path.home()/ ".local"/ "bin"/ "uv.exe")
  
  if default_uv.exists():
      return default_uv
      
  return None


def install_uv():

  print("Not found uv.")
  print("Starting the installation of uv...")

  command = [
    "powershell", "-ExecutionPolicy", "ByPass",
    "-Command", "irm https://astral.sh/uv/install.ps1 | iex"
  ]

  result = sp.run(
    command,
    text=True
  )

  if result.returncode != 0:
    raise RuntimeError("Failed to install uv.")

  # 설치 후 다시 탐색
  uv = find_uv()

  if uv is None:
    raise RuntimeError("Failed to find uv executable after installation.")

  return uv


def get_uv():
  uv = find_uv()
  if uv is not None:
      return uv
  return install_uv()


def get_uv_bin_dir(uv_path):
  result = sp.run(
    [str(uv_path), "tool", "dir", "--bin"],
    capture_output=True,
    text=True,
    check=True
    )
  return Path(result.stdout.strip())


def get_sleap_executable():
  uv = get_uv()
  bin_dir = get_uv_bin_dir(uv)
  sleap = (bin_dir / "sleap.exe")
  if sleap.exists() : return sleap
  return None


def is_sleap_installed():
    return (get_sleap_executable() is not None)

def install_sleap():

  uv = get_uv()

  print("Installing SLEAP...")
  print("The first installation may take some time.")

  command = [
    str(uv), "tool", "install",
    "--python", PYTHON_VERSION,
    f"sleap[nn]=={SLEAP_VERSION}",
    "--with", f"sleap-io=={SLEAP_IO_VERSION}",
    "--with", f"sleap-nn=={SLEAP_NN_VERSION}",
    "--torch-backend", "auto"
  ]

  result = sp.run(
    command,
    text=True
  )

  if result.returncode != 0:
    raise RuntimeError(
      "Failed to install SLEAP."
    )

  print("SLEAP installation completed.")


  def ensure_sleap():
  if not is_sleap_installed():
    install_sleap()

  sleap = get_sleap_executable()

  if sleap is None:
      raise RuntimeError(
        "SLEAP executable was not found."
      )
  return sleap


def select_save_path(
  title,
  defaultextension,
  filetypes
):
  root = tk.Tk()
  root.withdraw()

  file_path = filedialog.asksaveasfilename(
    title=title,
    defaultextension=defaultextension,
    filetypes=filetypes
  )

  root.destroy()

  if not file_path:
    return None

  print(
    f"The save path selected: {file_path}"
  )

  return file_path


def read_video_track() : 
	print("\n", "Preparing for running SLEAP... \n")
  print("\n","="*28,"\n   YOUR VIDEO FILE PATH & NAME  \n","   >> ",end="")
  root = tk.Tk()
  root.withdraw()

  video_path = filedialog.askopenfilename(
  title="CSV 파일 선택",
  filetypes=[
      ("CSV files", "*.csv"),
      ("txt files", "*.txt"),
      ("All files", "*.*")
      ]
  )

  if video_path == None:
      print("You chose NO file. ")
      exit("Please check again and retry the program. ")

  print(video_path,"\n\n", "="*28)
  
  print("\n","="*28,"\n   YOUR CENTROIDS_FILE PATH & NAME  \n","   >> ",end="")
  root = tk.Tk()
  root.withdraw()
  centroids_file = filedialog.askopenfilename(
  title="CSV 파일 선택",
  filetypes=[
      ("CSV files", "*.csv"),
      ("txt files", "*.txt"),
      ("All files", "*.*")
      ]
  )

  if centroids_file == None:
      print("You chose NO file. ")
      exit("Please check again and retry the program. ")

  print(centroids_file,"\n\n", "="*28)
  
  print("\n","="*28,"\n   YOUR CENTERED_INSTANCE_FILE PATH & NAME  \n","   >> ",end="")
  root = tk.Tk()
  root.withdraw()
  centered_instance_file = filedialog.askopenfilename(
  title="CSV 파일 선택",
  filetypes=[
      ("CSV files", "*.csv"),
      ("txt files", "*.txt"),
      ("All files", "*.*")
      ]
  )

  if centered_instance_file == None:
      print("You chose NO file. ")
      exit("Please check again and retry the program. ")

  print(centered_instance_file,"\n\n", "="*28)

return video_path, centroids_file, centered_instance_file


def run_sleap():
  # data_load.py 안에 ReadFile 클래스가 있다고 가정
  (
    video_path,
    centroids_file,
    centered_instance_file
  ) = read_video_track()

  # -----------------------------------------------------
  # SLEAP installation check
  # -----------------------------------------------------

  sleap = ensure_sleap()
  print("Running SLEAP...")

  # -----------------------------------------------------
  # Select .slp output path
  # -----------------------------------------------------

  label_path = select_save_path(
      title = "Save Tracking Results",
      defaultextension = ".slp",
      filetypes=[
        ("SLP files", "*.slp"),
        ("All files", "*.*")
      ]
  )

  if label_path is None:
    print("Tracking save canceled.")
    return None

  # -----------------------------------------------------
  # Run SLEAP tracking
  # -----------------------------------------------------

  try:
    sp.run(
      [
        str(sleap), "track",
        "-i", str(video_path),
        "-m", str(centroids_file),
        "-m", str(centered_instance_file),
        "--tracking", "-b", "8",
        "-n", "2",
        "-o", str(label_path)
      ], check=True
    )

  except sp.CalledProcessError as e : 
    raise RuntimeError(
        "Failed to run SLEAP tracking."
    ) from e

  # -----------------------------------------------------
  # Select raw CSV output path
  # -----------------------------------------------------

  csv_path = select_save_path(
    title="Save CSV Results",
    defaultextension=".csv",
    filetypes=[
        ("CSV files", "*.csv"),
        ("All files", "*.*")
    ]
  )

  if csv_path is None:
    print("CSV export canceled.")
    return None

  # -----------------------------------------------------
  # Export SLEAP results to CSV
  # -----------------------------------------------------

  try:
    sp.run(
      [
        str(sleap), "export", str(label_path),
        "-o", str(csv_path), "--csv-format", "instances"
      ], check=True
    )

  except sp.CalledProcessError as e:
    raise RuntimeError(
        "Failed to export SLEAP results."
    ) from e

  # -----------------------------------------------------
  # Read CSV
  # -----------------------------------------------------

  df = pd.read_csv(csv_path)
  print(df)

  # -----------------------------------------------------
  # Rename columns
  # -----------------------------------------------------

  rename_columns = {
    "nose.x": "nose_x",
    "nose.y": "nose_y",
    "nose.score": "nose_score",

    "left_ear.x": "left_ear_x",
    "left_ear.y": "left_ear_y",
    "left_ear.score": "left_ear_score",

    "right_ear.x": "right_ear_x",
    "right_ear.y": "right_ear_y",
    "right_ear.score": "right_ear_score",

    "torso.x": "torso_x",
    "torso.y": "torso_y",
    "torso.score": "torso_score",

    "tail_base.x": "tail_base_x",
    "tail_base.y": "tail_base_y",
    "tail_base.score": "tail_base_score"
  }

  df = df.rename(columns=rename_columns)

  # -----------------------------------------------------
  # Read FPS from video
  # -----------------------------------------------------

  cap = cv2.VideoCapture(str(video_path))

  if not cap.isOpened():
    raise RuntimeError(
      f"Could not open video: {video_path}"
    )

  fps = cap.get(cv2.CAP_PROP_FPS)
  cap.release()

  if fps <= 0:
    raise RuntimeError(
      "Could not determine video FPS."
    )

  print(f"Video FPS: {fps}")


  # -----------------------------------------------------
  # Convert frame index to time
  # -----------------------------------------------------

  if "frame_idx" not in df.columns:
    raise RuntimeError(
        "'frame_idx' column was not found "
        "in the SLEAP CSV file."
    )

  df["time_s"] = (df["frame_idx"] / fps)
  
  # -----------------------------------------------------
  # Choose columns
  # -----------------------------------------------------

  columns_to_save = [
    "frame_idx",
    "time_s",
    "track",
    "instance_idx",
    "instance_score",

    "nose_x",
    "nose_y",
    "nose_score",

    "left_ear_x",
    "left_ear_y",
    "left_ear_score",

    "right_ear_x",
    "right_ear_y",
    "right_ear_score",

    "torso_x",
    "torso_y",
    "torso_score",

    "tail_base_x",
    "tail_base_y",
    "tail_base_score"
  ]


  # 실제 존재하는 column만 저장
  columns_to_save = [
    col
    for col in columns_to_save
    if col in df.columns
  ]

  tracking_df = df[columns_to_save].copy()

  # -----------------------------------------------------
  # Save final CSV
  # -----------------------------------------------------

  tracking_save_path = select_save_path(
    title = "Save Final CSV Results",
    defaultextension = ".csv",
    filetypes=[
      ("CSV files", "*.csv"),
      ("All files", "*.*")
    ]
  )

  if tracking_save_path is None:
    print(
      "Final CSV save canceled."
    )
    # 파일 저장은 취소했어도
    # DataFrame 자체는 반환
    return tracking_df

  tracking_df.to_csv(
    tracking_save_path,
    index=False
  )

  print(tracking_df)
  return tracking_df
