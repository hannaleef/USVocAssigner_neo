"""Run pretrained SuperAnimal-TopViewMouse in a separate DeepLabCut environment.

CLI: python video/dlc_superanimal_worker.py --video VIDEO --out OUTDIR --mice 2
Outputs: tracking_superanimal.csv, a raw DeepLabCut .h5, and an optional
labeled visualization video. No SLEAP model is trained or modified.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

PARTS = {
    "nose": ("nose", "snout"),
    "left_ear": ("left_ear", "left_earbase"),
    "right_ear": ("right_ear", "right_earbase"),
    "torso": ("mid_back", "torso", "mouse_center"),
    "tail_base": ("tail_base", "tailbase"),
}


def normalize(value):
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def convert_h5(h5_file: Path, csv_file: Path, min_score: float = 0.25):
    """Convert DLC four-level MultiIndex HDF5 to untracked detections.

    The 'individuals' column is a detection slot, NOT a stable mouse ID.
    Intentionally do NOT write 'track' or 'track_id' here.
    """
    df = pd.read_hdf(h5_file, key="df_with_missing")
    if not isinstance(df.columns, pd.MultiIndex) or df.columns.nlevels != 4:
        raise ValueError(
            "Expected SuperAnimal multi-animal HDF5 with four column levels "
            "(scorer, individuals, bodyparts, coords). "
            f"Found {df.columns.names!r}."
        )
    names = [normalize(n) for n in df.columns.names]
    positions = {
        key: names.index(key) if key in names else fallback
        for key, fallback in (
            ("scorer", 0), ("individuals", 1),
            ("bodyparts", 2), ("coords", 3),
        )
    }
    # Slots can be assigned differently between successive frames.
    slots = list(dict.fromkeys(
        str(c[positions["individuals"]]) for c in df.columns
        if normalize(c[positions["individuals"]]) != "single"
    ))
    if not slots:
        raise ValueError("No individual detections in SuperAnimal HDF5.")

    lookup = {}
    for col in df.columns:
        slot = str(col[positions["individuals"]])
        part = normalize(col[positions["bodyparts"]])
        axis = normalize(col[positions["coords"]])
        if axis == "p":
            axis = "likelihood"
        lookup[(slot, part, axis)] = col

    sources = {}
    for slot in slots:
        per_slot = {}
        for target, aliases in PARTS.items():
            for alias in aliases:
                if ((slot, alias, "x") in lookup and
                        (slot, alias, "y") in lookup):
                    per_slot[target] = {
                        axis: lookup.get((slot, alias, axis))
                        for axis in ("x", "y", "likelihood")
                    }
                    break
        sources[slot] = per_slot
    if not any("nose" in plan and "torso" in plan
               for plan in sources.values()):
        present = sorted(set(normalize(c[positions["bodyparts"]])
                             for c in df.columns))
        raise ValueError(
            "Nose and mid_back/torso were not found in DLC output. "
            f"Available points: {present}"
        )

    rows = []
    for frame_idx, (_, frame) in enumerate(df.iterrows()):
        for slot_idx, slot in enumerate(slots):
            record = {
                "frame_idx": frame_idx,
                "instance_idx": slot_idx,
                "dlc_detection_slot": slot,
            }
            valid = 0
            for target in PARTS:
                x = y = score = np.nan
                source = sources[slot].get(target)
                if source:
                    try:
                        x = float(frame[source["x"]])
                        y = float(frame[source["y"]])
                        if source["likelihood"] is not None:
                            score = float(frame[source["likelihood"]])
                    except (TypeError, ValueError):
                        pass
                # Missing or uncertain coordinates are not imputed.
                if (np.isfinite(x) and np.isfinite(y) and
                        np.isfinite(score) and score >= min_score):
                    valid += 1
                else:
                    x = y = np.nan
                record[target + "_x"] = x
                record[target + "_y"] = y
                record[target + "_score"] = score
            # Skip entirely empty detection slots.
            if valid >= 2:
                rows.append(record)

    if not rows:
        raise ValueError("No valid mouse detections in this video.")
    result = pd.DataFrame(rows)
    result.to_csv(csv_file, index=False)
    return result


def _emit_progress(**data):
    """Machine-readable events for the parent GUI process."""
    print("USVOC_PROGRESS_JSON:" + json.dumps(data, ensure_ascii=False),
          flush=True)


def _hook_frame_progress(total_video_frames):
    """Publish real completed-frame counts from tqdm inside DLC.

    Hooks tqdm.update and tqdm.__iter__ because looping over tqdm's
    iterable may not call update on each frame. No inferred fake progress.
    """
    if total_video_frames <= 0:
        return lambda: None
    try:
        import tqdm.std
    except ImportError:
        return lambda: None

    cls = tqdm.std.tqdm
    original_update = cls.update
    original_iter = cls.__iter__
    state = {
        "bar": None, "pass_index": 0,
        "last_sent": 0.0, "last_count": -1,
    }
    tolerance = max(2, int(total_video_frames * 0.03))

    def _observe(bar, completed):
        try:
            total = int(bar.total or 0)
            if not total or abs(total - total_video_frames) > tolerance:
                return
            count = max(0, min(int(completed), total))
            if state["bar"] is not bar:
                state["bar"] = bar
                state["pass_index"] += 1
                state["last_sent"] = 0.0
                state["last_count"] = -1
            if count <= state["last_count"] and count != total:
                return
            now = time.monotonic()
            if (count >= total or now - state["last_sent"] >= 0.7
                    or state["last_count"] < 0):
                _emit_progress(
                    kind="frames",
                    pass_index=state["pass_index"],
                    completed=count, total=total,
                )
                state["last_sent"] = now
                state["last_count"] = count
        except Exception:
            # Monitoring errors must not affect prediction.
            pass

    def instrumented_update(bar, n=1):
        result = original_update(bar, n)
        _observe(bar, bar.n)
        return result

    def instrumented_iter(bar):
        completed_in_loop = 0
        for item in original_iter(bar):
            yield item
            # Reached after an item was consumed by the inference loop.
            completed_in_loop += 1
            _observe(bar, max(int(bar.n), completed_in_loop))

    cls.update = instrumented_update
    cls.__iter__ = instrumented_iter

    def restore():
        cls.update = original_update
        cls.__iter__ = original_iter

    return restore

def _video_frame_count(video):
    """Read frame metadata without decoding the full video."""
    try:
        import cv2
        capture = cv2.VideoCapture(str(video))
        try:
            if not capture.isOpened():
                return 0
            return int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            capture.release()
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mice", type=int, default=2)
    parser.add_argument("--min-score", type=float, default=0.25)
    args = parser.parse_args()
    video = Path(args.video).resolve()
    out = Path(args.out).resolve()
    if not video.is_file():
        parser.error("Video file does not exist.")
    if not 1 <= args.mice <= 30:
        parser.error("--mice must be between 1 and 30.")
    out.mkdir(parents=True, exist_ok=True)

    total_frames = _video_frame_count(video)
    _emit_progress(kind="stage", stage="model",
                   message="Loading/downloading pretrained model weights")
    # tqdm is used by DLC in some builds. Instrument genuine frame
    # updates, but do not fake progress when this instrumentation finds none.
    restore_monitor = _hook_frame_progress(total_frames)
    try:
        from deeplabcut.modelzoo.video_inference import video_inference_superanimal
        print("Starting SuperAnimal inference...", flush=True)
        video_inference_superanimal(
            videos=[str(video)],
            superanimal_name="superanimal_topviewmouse",
            model_name="hrnet_w32",
            detector_name="fasterrcnn_resnet50_fpn_v2",
            video_adapt=False,
            max_individuals=args.mice,
            dest_folder=str(out),
            create_labeled_video=True,
        )
    finally:
        restore_monitor()
    _emit_progress(kind="stage", stage="convert",
                   message="Converting inferred keypoints to CSV")
    # A unique OUTDIR per run is required. Never mix predictions from
    # different videos or treat a stale HDF5 as a fresh inference result.
    candidates = sorted(out.glob("*.h5"))
    candidates = [p for p in candidates if video.stem in p.name]
    if len(candidates) != 1:
        raise RuntimeError(
            "Expected exactly one SuperAnimal HDF5 for this video; "
            f"found {len(candidates)} in {out}. Files: {[p.name for p in candidates]}"
        )
    destination = out / "tracking_superanimal.csv"
    data = convert_h5(candidates[0], destination, args.min_score)
    (out / "tracking_superanimal_metadata.json").write_text(
        json.dumps({
            "source_video": str(video),
            "source_h5": str(candidates[0]),
            "tracking_csv": str(destination),
            "min_score": args.min_score,
            "n_detection_rows": len(data),
            "model": "superanimal_topviewmouse/hrnet_w32",
            "identity_warning": "Detection slots are NOT stable mouse identities.",
        }, indent=2), encoding="utf-8")
    _emit_progress(kind="stage", stage="complete",
                   message="Tracking CSV generated successfully")
    print("USVOC_TRACKING_CSV=" + str(destination), flush=True)


if __name__ == "__main__":
    main()
