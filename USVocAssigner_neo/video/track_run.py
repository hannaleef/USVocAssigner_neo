# [video/track_run.py] SLEAP CLI 실행 어댑터
# 입력: 영상 경로, centroid/centered-instance 모델, 출력 .slp 및 CSV 경로.
# 두 단계: SLEAP track으로 추론 -> export로 instances CSV 저장.
# 실행 파일/CLI 인자는 사용 중인 SLEAP 버전에서 반드시 확인한다.
# 이 함수는 동기식이므로 영상이 길면 별도 QThread/QRunnable에서 호출한다.

from pathlib import Path
# 외부 SLEAP 프로그램을 별도 프로세스로 실행하는 표준 모듈.
import subprocess
import pandas as pd

# 실행파일 경로와 모델 파일 경로는 실제 설치 환경에 맞게 전달한다.
def run_sleap(video_path, centroid_model, centered_model,
              output_slp, output_csv, sleap_executable="sleap"):
    """Track/export command syntax must be verified for installed SLEAP version."""
    # 1단계: 영상 추적 결과를 .slp로 생성; check=True라 실패 시 예외.
    subprocess.run([sleap_executable, "track", "-i", str(video_path),
                    "-m", str(centroid_model), "-m", str(centered_model),
                    "-o", str(output_slp)], check=True)
    # 2단계: .slp 추적 정보를 instances CSV로 내보낸다.
    subprocess.run([sleap_executable, "export", str(output_slp),
                    "-o", str(output_csv), "--csv-format", "instances"], check=True)
    # 결과 CSV를 DataFrame으로 읽어 track_data 후처리에 넘긴다.
    return pd.read_csv(output_csv)


def load_tracking_table(path, sleap_executable="sleap"):
    """SLEAP track data .slp 또는 export된 .csv를 pandas로 반환.

    .slp 입력은 CLI export 과정을 수행한다. 버전마다 인자가 달라질 수 있으므로
    설치 버전에서 sleap export --help 확인이 필요하다. GUI 워커에서 실행 권장.
    학습된 .h5 모델 파일 자체는 tracking 결과 테이블이 아니다.
    """
    from tempfile import TemporaryDirectory
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() != ".slp":
        raise ValueError("Tracking input must be .slp or instances CSV.")
    with TemporaryDirectory(prefix="usvoc_sleap_") as tmp:
        result_csv = Path(tmp) / "instances.csv"
        try:
            subprocess.run([
                sleap_executable, "export", str(path), "-o", str(result_csv),
                "--csv-format", "instances"
            ], check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "SLEAP CLI not found; export an instances CSV in SLEAP first."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"SLEAP export failed: {exc.stderr}") from exc
        return pd.read_csv(result_csv)
