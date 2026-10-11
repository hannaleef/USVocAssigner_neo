# [audio/data_load.py] 마이크 채널 단위 데이터 관리
# 5분 * 250 kS/s * 4채널은 원본만도 수백 MB 이상이므로
# 가능하면 mmap=True로 WAV를 읽어 '전체 파일의 float 복사'를 피한다.
# AudioData.samples는 원본 PCM 또는 float 배열이며, 분석 시 필요한 부분만
# normalized float32로 변환한다. 이 값은 물리적 전압[V]이 아니다.

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import wavfile


@dataclass(frozen=True)
class USVEvent:
    """녹음 시작점 기준 USV 시간구간 + 선택적인 주파수 범위.

    fmin_hz/fmax_hz가 제공되면 해당 USV 라벨의 주파수 대역만 분석하고
    그래프 ID 주석을 fmax_hz 바로 위에 배치한다. 단위는 Hz와 초(s).
    """
    onset_s: float
    offset_s: float
    fmin_hz: float | None = None
    fmax_hz: float | None = None

    def __post_init__(self):
        if (not np.isfinite(self.onset_s) or not np.isfinite(self.offset_s)
            or self.onset_s < 0 or self.offset_s <= self.onset_s):
            raise ValueError("USV onset/offset must satisfy 0 <= onset < offset")
        if (self.fmin_hz is None) != (self.fmax_hz is None):
            raise ValueError("Frequency bounds must be both provided or both omitted.")
        if self.fmin_hz is not None:
            if (not np.isfinite(self.fmin_hz) or not np.isfinite(self.fmax_hz)
                or self.fmin_hz < 0 or self.fmax_hz <= self.fmin_hz):
                raise ValueError("Invalid USV frequency interval in Hz.")


@dataclass
class AudioData:
    """마이크 채널 하나의 원본 데이터. channel_id는 실제 mouse_id가 아니다."""
    channel_id: str
    path: Path | None
    sr: int
    samples: np.ndarray  # raw PCM (possibly memory mapped), or float samples

    @property
    def duration_s(self) -> float:
        """샘플 개수를 sampling rate로 나누어 녹음 길이[초] 반환."""
        return len(self.samples) / self.sr

    def slice_float(self, start_sample: int, stop_sample: int) -> np.ndarray:
        """필요 구간만 정규화 float32 배열로 가져온다. 전체 WAV 복사는 하지 않는다."""
        start = max(0, int(start_sample))
        stop = min(len(self.samples), int(stop_sample))
        raw = self.samples[start:stop]

        if np.issubdtype(raw.dtype, np.unsignedinteger):
            # 8-bit PCM (uint8 등)은 가운데 값(예: 128)이 0 신호이다.
            midpoint = float(1 << (np.iinfo(raw.dtype).bits - 1))
            return (raw.astype(np.float32) - midpoint) / midpoint

        if np.issubdtype(raw.dtype, np.signedinteger):
            # int16, int32 PCM: 해당 비트 수의 부호 정수 범위를 [-1,1)에 대응.
            # scipy.io.wavfile의 24-bit PCM -> left-justified int32도 처리한다.
            scale = float(1 << (np.iinfo(raw.dtype).bits - 1))
            return raw.astype(np.float32) / scale

        if np.issubdtype(raw.dtype, np.floating):
            # float WAV나 합성 테스트 데이터는 이미 상대 진폭이라고 가정.
            return raw.astype(np.float32, copy=False)

        raise TypeError(f"Unsupported WAV sample dtype: {raw.dtype}")


def load_audio(path: str | Path, channel_id: str, csv_sr=None) -> AudioData:
    """WAV 또는 명시된 샘플레이트를 가진 단일 파형 CSV를 AudioData로 변환.

    CSV는 숫자 열 하나 또는 'amplitude'/'voltage' 열을 가진다고 가정한다.
    실제 DAQ CSV에 시간 컬럼이 있다면 시간 간격을 검증한 뒤 조정해야 한다.
    """
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            sr, samples = wavfile.read(path, mmap=True)
        except (ValueError, OSError):
            sr, samples = wavfile.read(path, mmap=False)
    elif path.suffix.lower() == ".csv":
        if csv_sr is None or int(csv_sr) <= 0:
            raise ValueError("CSV waveform requires sampling rate (Hz).")
        df = pd.read_csv(path)
        if "amplitude" in df:
            series = df["amplitude"]
        elif "voltage" in df:
            series = df["voltage"]
        elif df.shape[1] == 1:
            series = df.iloc[:, 0]
        else:
            raise ValueError("CSV needs one waveform column, amplitude or voltage.")
        samples = pd.to_numeric(series, errors="raise").to_numpy(dtype=np.float32)
        sr = int(csv_sr)
    else:
        raise ValueError("Audio format must be WAV or waveform CSV.")
    if samples.ndim != 1:
        raise ValueError(f"{path.name}: one mono channel per audio input.")
    if sr <= 0 or len(samples) == 0:
        raise ValueError(f"{path.name}: invalid sample rate or empty recording.")
    return AudioData(channel_id, path, int(sr), samples)


def load_labels(path, onset="onset", offset="offset", unit="s") -> list[USVEvent]:
    """CSV/TXT 라벨의 onset/offset을 초(s) 단위 USVEvent로 통일.

    지원: onset/offset, start/end, onset_s/offset_s, Time Begin/Time End.
    열 명칭/숫자 단위가 불분명하면 사용자가 입력 형식을 수정해야 한다.
    TXT는 쉼표/탭/공백/세미콜론으로 구분된 표를 허용한다.
    """
    path = Path(path)
    if path.suffix.lower() not in (".csv", ".txt"):
        raise ValueError("Label file must be CSV or TXT.")
    df = pd.read_csv(path, sep=None, engine="python")
    names = {str(col).strip().lower().replace(" ", "_"): col for col in df.columns}
    options = [(onset, offset), ("onset_s", "offset_s"),
               ("start", "end"), ("time_begin", "time_end")]
    selected = next(((names[a], names[b]) for a,b in options
                     if a in names and b in names), None)
    if selected is None:
        raise ValueError("Label header needs onset/offset (seconds) or start/end.")
    if unit not in ("s", "ms"):
        raise ValueError("Label units must be s or ms; automatic unit guessing is unsafe.")
    multiplier = 0.001 if unit == "ms" else 1.0
    # 선택적 주파수 열: Avisoft 등에서 Hz 또는 kHz가 제공될 수 있다.
    # 파일 헤더에 명시한 주파수 단위만 해석하며 Hz/kHz를 추측하지 않는다.
    freq_options = [
        ("fmin_hz", "fmax_hz", 1.0),
        ("freq_min_hz", "freq_max_hz", 1.0),
        ("low_freq_hz", "high_freq_hz", 1.0),
        ("fmin_khz", "fmax_khz", 1000.0),
        ("freq_min_khz", "freq_max_khz", 1000.0),
    ]
    freq_columns = next(((names[a], names[b], scale)
                         for a,b,scale in freq_options if a in names and b in names), None)
    out = []
    for index, (a,b) in enumerate(zip(df[selected[0]], df[selected[1]])):
        if freq_columns is None:
            out.append(USVEvent(float(a)*multiplier, float(b)*multiplier))
        else:
            lo_col, hi_col, scale = freq_columns
            lo, hi = df.iloc[index][lo_col], df.iloc[index][hi_col]
            if pd.isna(lo) or pd.isna(hi):
                out.append(USVEvent(float(a)*multiplier, float(b)*multiplier))
            else:
                out.append(USVEvent(float(a)*multiplier, float(b)*multiplier,
                                    float(lo)*scale, float(hi)*scale))
    return sorted(out, key=lambda event: event.onset_s)

# TODO: 실제 장비 gain, sensitivity, frequency response calibration.
# TODO: Avisoft 라벨 CSV의 서로 다른 열 이름/단위에 대한 로더 어댑터.