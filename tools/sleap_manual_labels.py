#!/usr/bin/env python3
"""Prepare manual mouse-pose annotations for USVocAssigner / SLEAP.

Outputs:
  - manual_tracking.csv: frame-level, long-format rows with the columns expected
    by the Notion track_run.py -> track_data.py pipeline.
  - manual_labels.slp: SLEAP human-labeled data (NOT trained model weights).
  - contact_sheet.jpg, sample_frames/: visual video decoding diagnostics.

No inferred coordinates or model confidence scores are fabricated.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

NODES = ("nose", "left_ear", "right_ear", "torso", "tail_base")
CSV_FIELDS = ["frame_idx", "time_s", "track", "mouse_id",
              "instance_idx", "instance_score"]
for node in NODES:
    CSV_FIELDS += [f"{node}_x", f"{node}_y", f"{node}_score"]


def video_info(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(
            "OpenCV could not open this video. Use ffprobe to check its codec, "
            "or transcode to H.264 MP4 with the command in README."
        )
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc_num = int(cap.get(cv2.CAP_PROP_FOURCC))
    fourcc = "".join(chr((fourcc_num >> (8 * i)) & 255) for i in range(4))
    cap.release()
    if not math.isfinite(fps) or fps <= 0 or frames <= 0:
        raise RuntimeError(
            f"Invalid video metadata: fps={fps}, frames={frames}. "
            "Try H.264 transcoding before annotation."
        )
    return dict(fps=fps, frames=frames, width=width, height=height,
                fourcc=fourcc, duration_s=frames / fps,
                note="frame_idx / fps is nominal; VFR clips need timestamp review")


def ffprobe_info(video_path: Path):
    if not shutil.which("ffprobe"):
        return {"status": "ffprobe not installed"}
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,profile,width,height,"
           "r_frame_rate,avg_frame_rate,nb_frames,pix_fmt",
           "-of", "json", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout}


def get_frame(cap, frame_idx: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Failed to decode video frame {frame_idx}.")
    return frame


def sample_indices(info, args):
    if args.events:
        with open(args.events, "r", encoding="utf-8-sig", newline="") as f:
            records = list(csv.DictReader(f))
        if not records:
            raise ValueError("USV events CSV contains no rows.")
        header = records[0].keys()
        onset_name = next((name for name in ("onset_s", "onset", "start_s", "start")
                           if name in header), None)
        offset_name = next((name for name in ("offset_s", "offset", "end_s", "end")
                            if name in header), None)
        if not onset_name or not offset_name:
            raise ValueError("Events CSV needs onset_s/offset_s columns in seconds.")
        seconds = [(float(r[onset_name]) + float(r[offset_name])) / 2.0
                   for r in records if r.get(onset_name) and r.get(offset_name)]
    else:
        start = max(0.0, args.start)
        end = info["duration_s"] - 1.0 / info["fps"]
        if args.end is not None:
            end = min(end, args.end)
        if end < start:
            raise ValueError("end time precedes start time.")
        seconds = np.linspace(start, end, args.max_frames).tolist()

    indices = sorted(set(max(0, min(info["frames"] - 1,
                                  round(t * info["fps"])))
                         for t in seconds))
    if len(indices) > args.max_frames:
        pick = np.linspace(0, len(indices) - 1,
                           args.max_frames).round().astype(int)
        indices = [indices[i] for i in pick]
    if not indices:
        raise ValueError("No frames were selected.")
    return indices


def save_samples(video_path, outdir, info, indices):
    cap = cv2.VideoCapture(str(video_path))
    sample_dir = outdir / "sample_frames"
    sample_dir.mkdir(parents=True, exist_ok=True)
    tiles = []
    for idx in indices:
        try:
            frame = get_frame(cap, idx)
        except RuntimeError as exc:
            print("WARNING:", exc, file=sys.stderr)
            continue
        cv2.imwrite(str(sample_dir / f"frame_{idx:08d}.jpg"), frame)
        h, w = frame.shape[:2]
        scale = min(450 / w, 280 / h)
        tile = cv2.resize(frame, (round(w * scale), round(h * scale)))
        canvas = np.zeros((315, 470, 3), dtype=np.uint8)
        canvas[26:26 + tile.shape[0], 10:10 + tile.shape[1]] = tile
        cv2.putText(canvas, f"Frame {idx}; {idx/info['fps']:.3f} s",
                    (10, 19), cv2.FONT_HERSHEY_SIMPLEX, .55,
                    (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(canvas)
    cap.release()
    if tiles:
        columns = min(3, len(tiles))
        rows = math.ceil(len(tiles) / columns)
        sheet = np.zeros((rows * 315, columns * 470, 3), dtype=np.uint8)
        for n, tile in enumerate(tiles):
            row, col = divmod(n, columns)
            sheet[row * 315:(row + 1) * 315,
                  col * 470:(col + 1) * 470] = tile
        cv2.imwrite(str(outdir / "contact_sheet.jpg"), sheet)
        print("Saved:", outdir / "contact_sheet.jpg")


def annotate_frame(frame, idx, info, mice):
    h, w = frame.shape[:2]
    scale = min(1.0, 1300 / w, 850 / h)
    resized = cv2.resize(frame, (round(w * scale), round(h * scale)))
    n_per_mouse = len(NODES)
    total = mice * n_per_mouse
    points = np.full((mice, n_per_mouse, 2), np.nan, dtype=np.float64)
    state = {"position": 0, "points": points}

    def callback(event, x, y, flags, userdata):
        if event == cv2.EVENT_LBUTTONDOWN and state["position"] < total:
            mouse_idx, node_idx = divmod(state["position"], n_per_mouse)
            state["points"][mouse_idx, node_idx] = (x / scale, y / scale)
            state["position"] += 1

    title = "Manual USV keypoints"
    cv2.setMouseCallback(title, callback)
    while True:
        canvas = resized.copy()
        for mouse_idx in range(mice):
            for node_idx in range(n_per_mouse):
                p = points[mouse_idx, node_idx]
                if not np.isfinite(p).all():
                    continue
                xy = (round(p[0] * scale), round(p[1] * scale))
                color = (0, 255, 255) if mouse_idx == 0 else (255, 180, 0)
                cv2.circle(canvas, xy, 4, color, -1)
                cv2.putText(canvas, NODES[node_idx][:4],
                            (xy[0] + 5, xy[1] - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, .4,
                            color, 1, cv2.LINE_AA)
        if state["position"] < total:
            mouse_idx, node_idx = divmod(state["position"], n_per_mouse)
            instruction = f"mouse_{mouse_idx}: click {NODES[node_idx]}"
        else:
            instruction = "Completed: ENTER to save or B to undo"
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 58),
                      (0, 0, 0), -1)
        cv2.putText(canvas, f"frame {idx} / {idx/info['fps']:.3f}s | {instruction}",
                    (8, 21), cv2.FONT_HERSHEY_SIMPLEX, .55,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, "L-click=point; S=missing; B=undo; N=skip frame; Q=finish",
                    (8, 48), cv2.FONT_HERSHEY_SIMPLEX, .5,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(title, canvas)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("s") and state["position"] < total:
            state["position"] += 1
        elif key == ord("b") and state["position"] > 0:
            state["position"] -= 1
            mouse_idx, node_idx = divmod(state["position"], n_per_mouse)
            points[mouse_idx, node_idx] = np.nan
        elif key == ord("n"):
            return "skip", points
        elif key in (ord("q"), 27):
            return "quit", points
        elif key in (10, 13) and state["position"] == total:
            return "save", points


def rows_for_frame(frame_idx, info, points):
    result = []
    for mouse_idx, pose in enumerate(points):
        # At least nose and torso must be labeled to use orientation.
        if not np.isfinite(pose[0]).all() or not np.isfinite(pose[3]).all():
            print(f"WARNING frame={frame_idx} mouse={mouse_idx}: missing nose/torso.")
        row = dict(frame_idx=frame_idx, time_s=frame_idx / info["fps"],
                   track=f"mouse_{mouse_idx}", mouse_id=f"mouse_{mouse_idx}",
                   instance_idx=mouse_idx, instance_score="")
        for node_idx, node in enumerate(NODES):
            xy = pose[node_idx]
            row[f"{node}_x"] = float(xy[0]) if np.isfinite(xy[0]) else ""
            row[f"{node}_y"] = float(xy[1]) if np.isfinite(xy[1]) else ""
            row[f"{node}_score"] = ""  # Manual labels have no model confidence.
        result.append(row)
    return result


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as out:
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_slp(rows, video_path, outdir):
    try:
        import sleap_io as sio
    except ImportError:
        print("WARNING: sleap-io unavailable; saved CSV only. "
              "Install sleap-io==0.7.0 to export .slp.")
        return
    skeleton = sio.Skeleton(
        nodes=list(NODES),
        edges=[("nose", "torso"), ("torso", "tail_base"),
               ("nose", "left_ear"), ("nose", "right_ear")]
    )
    video = sio.load_video(str(video_path))
    tracks = {}
    groups = {}
    for r in rows:
        groups.setdefault(int(r["frame_idx"]), []).append(r)
    labeled_frames = []
    for frame_idx, group in sorted(groups.items()):
        instances = []
        for row in group:
            p = np.full((len(NODES), 2), np.nan)
            for n, node in enumerate(NODES):
                x, y = row[f"{node}_x"], row[f"{node}_y"]
                if x != "" and y != "":
                    p[n] = (float(x), float(y))
            if not np.isfinite(p).any():
                continue
            name = row["track"]
            if name not in tracks:
                tracks[name] = sio.Track(name)
            instances.append(sio.Instance.from_numpy(
                p, skeleton=skeleton, track=tracks[name]))
        if instances:
            labeled_frames.append(sio.LabeledFrame(
                video=video, frame_idx=frame_idx, instances=instances))
    if not labeled_frames:
        return
    labels = sio.Labels(labeled_frames=labeled_frames)
    output = outdir / "manual_labels.slp"
    labels.save(str(output))
    print("Saved:", output, "(human labels, NOT trained weights)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("sleap_output"))
    parser.add_argument("--mode", choices=("inspect", "frames", "annotate"),
                        default="inspect")
    parser.add_argument("--events", type=Path, help="CSV with onset_s,offset_s seconds")
    parser.add_argument("--mice", type=int, default=2)
    parser.add_argument("--max-frames", type=int, default=24)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    args = parser.parse_args()
    args.video = args.video.resolve()
    args.outdir.mkdir(parents=True, exist_ok=True)
    if not args.video.is_file():
        parser.error(f"Video not found: {args.video}")
    if args.mice < 1 or args.max_frames < 1:
        parser.error("--mice and --max-frames must be positive.")

    probe = ffprobe_info(args.video)
    print("ffprobe:", json.dumps(probe, ensure_ascii=False, indent=2))
    try:
        info = video_info(args.video)
    except Exception as exc:
        print("Video decoding/preflight FAILED:", exc, file=sys.stderr)
        print("If ffprobe reports HEVC/h265 or OpenCV fails, try:", file=sys.stderr)
        print(f'ffmpeg -i "{args.video}" -c:v libx264 -pix_fmt yuv420p'
              ' -crf 18 -preset fast -an "video_h264.mp4"', file=sys.stderr)
        raise SystemExit(2)
    print("OpenCV:", json.dumps(info, indent=2))
    with open(args.outdir / "video_info.json", "w", encoding="utf-8") as f:
        json.dump(dict(opencv=info, ffprobe=probe), f, indent=2)

    selected = sample_indices(info, args)
    print("Selected frame indices:", selected)
    if args.mode == "inspect":
        cap = cv2.VideoCapture(str(args.video))
        for idx in [selected[0], selected[len(selected)//2], selected[-1]]:
            try:
                frame = get_frame(cap, idx)
                print(f"Decoded frame {idx}: shape={frame.shape}")
            except Exception as exc:
                print(f"FAILED frame {idx}: {exc}")
        cap.release()
        return

    if args.mode == "frames":
        save_samples(args.video, args.outdir, info, selected)
        return

    cap = cv2.VideoCapture(str(args.video))
    cv2.namedWindow("Manual USV keypoints", cv2.WINDOW_NORMAL)
    rows = []
    output_csv = args.outdir / "manual_tracking.csv"
    try:
        for idx in selected:
            try:
                frame = get_frame(cap, idx)
            except Exception as exc:
                print(f"WARNING: {exc}")
                continue
            action, points = annotate_frame(frame, idx, info, args.mice)
            if action == "quit":
                break
            if action == "skip":
                continue
            rows.extend(rows_for_frame(idx, info, points))
            write_csv(rows, output_csv)  # save incrementally
            print(f"Saved {len(rows)} rows -> {output_csv}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
    if rows:
        write_slp(rows, args.video, args.outdir)
        print("Completed:", output_csv)
    else:
        print("No annotations saved.")


if __name__ == "__main__":
    main()
