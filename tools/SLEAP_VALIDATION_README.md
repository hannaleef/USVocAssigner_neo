# SLEAP preliminary validation kit (2026-10-13)

This is a **local execution kit**, not a pretrained model. The original video was uploaded to ChatGPT, but the ChatGPT video-processing runtime returned a `ClientError` **before** OpenCV/FFmpeg could inspect or decode frames. No genuine keypoint annotations or trained model weights could be produced from that video here.

The `tools/sleap_manual_labels.py` script helps you inspect the video on your Windows machine, generate a contact sheet, manually mark real keypoints, and export a CSV usable with the Notion USVocAssigner schema plus an **untrained, human-labeled** SLEAP `.slp` project.

## 1. Install in a local Python environment

In PowerShell, from the cloned repository:

```powershell
py -3.13 -m venv .venv_annotation
.\.venv_annotation\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install opencv-python numpy "sleap-io==0.7.0"
```

Do not edit the automatically generated `ui_mainwindow.py`. The utility is standalone and does not modify the app.

## 2. Diagnose your actual uploaded video

```powershell
python tools/sleap_manual_labels.py --mode inspect --video "C:\path\to\WIN_20261012_15_19_10_Pro.mp4" --outdir validation
```

This writes `validation/video_info.json` and prints FPS, frame count, fourcc and optional FFprobe codec details, plus tests decoding of representative frames. A file successfully opening does **not** prove that every frame decodes correctly.

If OpenCV cannot decode it, install FFmpeg, and run:

```powershell
ffprobe -v error -show_entries stream=codec_name,profile,pix_fmt,avg_frame_rate,r_frame_rate -of default=noprint_wrappers=1 "C:\path\to\WIN_20261012_15_19_10_Pro.mp4"
ffmpeg -i "C:\path\to\WIN_20261012_15_19_10_Pro.mp4" -c:v libx264 -pix_fmt yuv420p -crf 18 -preset fast -an "video_h264.mp4"
```

Only use `video_h264.mp4` after checking the original video timestamps. Transcoding and any change in frame count/FPS must be reflected in synchronization and label frame indices.

## 3. Export frames to review

```powershell
python tools/sleap_manual_labels.py --mode frames --video "C:\path\to\WIN_20261012_15_19_10_Pro.mp4" --outdir validation --max-frames 12
```

Outputs `validation/contact_sheet.jpg` and still frames in `validation/sample_frames/`. Review whether animals and keypoints can be distinguished. You can also share the contact sheet for visual assessment.

## 4. Annotate real poses (without waiting for model training)

Create a list of USV time windows if available:

```csv
onset_s,offset_s
3.52,3.61
5.80,5.92
8.14,8.21
```

Then run:

```powershell
python tools/sleap_manual_labels.py --mode annotate --video "C:\path\to\WIN_20261012_15_19_10_Pro.mp4" --events "C:\path\to\usv_events.csv" --outdir validation --mice 2 --max-frames 24
```

Or, with no events file, evenly sample frames:

```powershell
python tools/sleap_manual_labels.py --mode annotate --video "C:\path\to\WIN_20261012_15_19_10_Pro.mp4" --outdir validation --mice 2 --max-frames 24
```

For each frame, the window requests the following landmarks **in this exact order for each mouse**:

1. `nose`
2. `left_ear`
3. `right_ear`
4. `torso`
5. `tail_base`

Left-click to set a point; `S` marks a point as missing; `B` undoes; `N` skips a frame; `Q` exits while retaining prior completed frames; `Enter` saves a completed frame and proceeds. Keep `mouse_0` and `mouse_1` consistent across all labeled frames. If identities become ambiguous or a mouse is occluded, **mark missing or skip rather than inventing coordinates**.

Outputs:

- `validation/manual_tracking.csv`: `frame_idx`, `time_s`, `track`, `mouse_id`, `instance_idx`, blank model scores, and per-node `x/y` columns. The schema matches the Notion `track_run.py`'s downstream tracking columns. Manual annotations carry no artificial confidence scores.
- `validation/manual_labels.slp`: SLEAP human-labeled project; generated only if `sleap-io` imports correctly.
- `validation/video_info.json`: video metadata and FFprobe information.

`manual_labels.slp` is **not** the `centroid` or `centered-instance` trained model directory required by `track_run.py`.

## 5. Train actual SLEAP top-down models

Open `manual_labels.slp` in SLEAP 1.6.3, review all annotations, correct mistaken identity/pose labels, and use **Predict → Run Training**. Select the **top-down centroid + centered-instance** pipeline with `torso` as anchor. SLEAP automatically trains the two stages sequentially. A small manually labeled set can produce a prototype but is **not proof of tracking accuracy**.

The top-down pipeline's two resulting **trained model directories** (each with `best.ckpt` and `training_config.yaml`) are the inputs for the two `-m` arguments in your Notion `track_run.py`. Training must actually run to produce those directories. Reference: https://docs.sleap.ai/dev/tutorial/training-a-model/

Check the local SLEAP 1.6.3 interface and CLI help, as different SLEAP / sleap-nn versions use different command syntaxes.

## 6. Compatibility and scientific limitations

- `track_run.py` in Notion expects `nose`, `left_ear`, `right_ear`, `torso`, `tail_base` plus `frame_idx/time_s/track`, which this utility exports.
- The older Notion `track_data.py` contains syntax and tracking issues; notably `create_track()` lacks a colon, `matches.append()` takes multiple positional values, and `track_id`/`mouse_id` naming is inconsistent. Verify the current local implementation before calling it directly.
- Pose labels alone cannot establish actual USV emitter identity. Obtain independent ground-truth emitter IDs before claiming improved assignment accuracy.
- `frame_idx / fps` is only nominal time for variable-frame-rate recordings. Verify audio-video synchronization in the application.
- Pixel-coordinate distances and angles do not automatically provide true 3-D acoustic geometry. Capture actual microphone position and image-plane calibration before physical correction.
- A manually annotated subset supports a preliminary analysis; a trained model must be validated on held-out frames, especially during occlusions and animal crossings.
