"""USVocAssigner DeepLabCut automatic runtime manager (Windows x64).

First run: bundled uv.exe downloads managed CPython 3.12 and installs a
private DeepLabCut 3.0.0 environment under LOCALAPPDATA. No admin rights,
manual Python installation, PowerShell script, or user-managed venv required.

Subsequent runs reuse that environment. GUI must run this in a QRunnable.
The pretrained SuperAnimal weights are downloaded by DeepLabCut on first
inference; those weights are NOT created by this module.

For PyInstaller onedir bundle:
  --add-binary "<absolute path to uv.exe>;vendor"
  --add-data "video/dlc_superanimal_worker.py;video"
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable

RUNTIME_REVISION = "superanimal-cpu-v1"
DLC_DEPENDENCIES = ["deeplabcut[modelzoo]==3.0.0", "tables>=3.9"]
PYTHON_VERSION = "3.12"


def runtime_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "USVocAssigner" / "model_runtimes" / RUNTIME_REVISION


def runtime_python() -> Path:
    return runtime_root() / ".venv" / "Scripts" / "python.exe"


def is_superanimal_ready() -> bool:
    marker = runtime_root() / "ready.json"
    if not (runtime_python().is_file() and marker.is_file()):
        return False
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
        return state.get("runtime_revision") == RUNTIME_REVISION
    except (OSError, ValueError):
        return False


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # PyInstaller onedir _internal directory.
    return Path(__file__).resolve().parents[1]


def _get_uv() -> Path:
    bundled = _resource_root() / "vendor" / "uv.exe"
    if bundled.is_file():
        return bundled
    # Developer-only fallback for running from source.
    found = shutil.which("uv")
    if found:
        return Path(found)
    raise RuntimeError(
        "Bundled vendor/uv.exe is missing. The distributor must package "
        "an official uv.exe with PyInstaller. End users must not install "
        "DeepLabCut or Python manually."
    )


def _subprocess_logged(
    cmd: list[str],
    log_path: Path,
    label: str,
    env: dict[str, str],
    progress: Callable[[object], None] | None = None,
    stream_progress: bool = False,
) -> None:
    """Run hidden subprocess with logfile; optionally relay verified progress.

    The DLC worker writes JSON only after its real tqdm frame counter updates.
    No progress is inferred from a subprocess merely being alive.
    """
    if progress is not None:
        progress({"kind": "stage", "message": label})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as logfile:
        logfile.write("\n=== " + label + " ===\n")
        logfile.write("Command: " + " ".join(cmd[:3]) + " [arguments omitted]\n")
        logfile.flush()
        try:
            if not stream_progress:
                process = subprocess.run(
                    cmd, stdout=logfile, stderr=subprocess.STDOUT,
                    env=env, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return_code = process.returncode
            else:
                # This process is already executing in AutoDLCJob's QRunnable.
                # Stream structured events back to Qt without blocking the UI.
                child_env = env.copy()
                child_env["PYTHONUNBUFFERED"] = "1"
                with subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=child_env, text=True, encoding="utf-8",
                    errors="replace", bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                ) as child:
                    if child.stdout is not None:
                        for line in child.stdout:
                            logfile.write(line)
                            logfile.flush()
                            sentinel = "USVOC_PROGRESS_JSON:"
                            if sentinel not in line or progress is None:
                                continue
                            payload_text = line.split(sentinel, 1)[1].strip()
                            # CR-based tqdm text before a JSON message
                            # must not contaminate JSON parsing.
                            try:
                                payload = json.loads(payload_text)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if isinstance(payload, dict):
                                progress(payload)
                    return_code = child.wait()
        except OSError as exc:
            raise RuntimeError(
                f"{label}: executable could not start: {exc}"
            ) from exc
        if return_code != 0:
            raise RuntimeError(
                f"{label} failed (exit {return_code}). "
                f"See installation/inference log: {log_path}"
            )

def ensure_superanimal_environment(
    progress: Callable[[object], None] | None = None,
) -> Path:
    """Install only once; recreate an incomplete environment on retry.

    Do not run concurrently from two processes. App GUI disables the launch
    button until completion; distributor should use a process-wide installer
    lock if multiple app instances will be allowed.
    """
    if sys.platform != "win32":
        raise RuntimeError("This automatic installer targets Windows only.")
    if is_superanimal_ready():
        return runtime_python()

    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    uv = _get_uv()
    venv = root / ".venv"
    log = root / "install.log"
    env = os.environ.copy()
    env.update({
        "UV_PYTHON_INSTALL_DIR": str(root / "managed_python"),
        "UV_CACHE_DIR": str(root / "uv_cache"),
        "UV_LINK_MODE": "copy",
        "PYTHONNOUSERSITE": "1",
        "HF_HOME": str(root / "hf_cache"),
    })

    # Failed prior installs never become "ready"; repair on retry.
    if venv.exists():
        shutil.rmtree(venv)
    ready = root / "ready.json"
    ready.unlink(missing_ok=True)

    _subprocess_logged(
        [str(uv), "--no-config", "venv", "--python", PYTHON_VERSION,
         "--managed-python", str(venv)],
        log, "1/3 · Download Python 3.12 and create private environment",
        env, progress,
    )
    python = runtime_python()
    if not python.exists():
        raise RuntimeError(f"Managed Python missing: {python}")
    _subprocess_logged(
        [str(uv), "--no-config", "pip", "install", "--python",
         str(python), *DLC_DEPENDENCIES],
        log, "2/3 · Download DeepLabCut and dependencies", env, progress,
    )
    _subprocess_logged(
        [str(python), "-c",
         "import deeplabcut, torch, pandas, tables; "
         "from deeplabcut.modelzoo.video_inference "
         "import video_inference_superanimal; "
         "print('DLC runtime OK')"],
        log, "3/3 · Validate installation", env, progress,
    )
    ready.write_text(
        json.dumps({
            "runtime_revision": RUNTIME_REVISION,
            "python": PYTHON_VERSION,
            "dependencies": DLC_DEPENDENCIES,
        }, indent=2),
        encoding="utf-8",
    )
    return python


def run_superanimal_tracking(
    video_path: str,
    output_root: str,
    n_mice: int = 2,
    min_score: float = 0.25,
    progress: Callable[[object], None] | None = None,
) -> str:
    """Perform one-click preparation + SuperAnimal inference."""
    video = Path(video_path).resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Video does not exist: {video}")
    if not (1 <= n_mice <= 30):
        raise ValueError("n_mice must be 1..30.")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    worker = _resource_root() / "video" / "dlc_superanimal_worker.py"
    if not worker.is_file():
        raise RuntimeError(
            "Missing bundled video/dlc_superanimal_worker.py. "
            "Include it with PyInstaller --add-data."
        )

    python = ensure_superanimal_environment(progress)
    out = root / ("superanimal_" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update({
        "HF_HOME": str(runtime_root() / "hf_cache"),
        "PYTHONNOUSERSITE": "1",
    })
    _subprocess_logged(
        [str(python), str(worker), "--video", str(video),
         "--out", str(out), "--mice", str(n_mice),
         "--min-score", str(min_score)],
        out / "inference.log",
        "4/4 · Download model weights if needed and infer mouse poses",
        env, progress, stream_progress=True,
    )
    csv_path = out / "tracking_superanimal.csv"
    if not csv_path.is_file():
        raise RuntimeError(
            f"Prediction finished but tracking CSV is missing. "
            f"Check {out / 'inference.log'}"
        )
    if progress:
        progress("SuperAnimal tracking complete")
    return str(csv_path)
