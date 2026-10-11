# [audio/aud_process.py] 다채널 그래프와 ratio-only 분석에 공통으로 사용하는 STFT
# GUI는 각 채널의 짧은 구간(기본 3초)만 읽어 스펙트로그램을 계산한다.
# 데이터 단위: 상대 디지털 진폭 -> 20log10(|STFT|); 이는 dB SPL이 아니다.
# 화면에서 표시할 시간 bin 수만 줄이는 옵션(max_time_bins)은
# 분석용 특징 함수(event_level)에는 적용하지 않는다.

import numpy as np
from scipy.signal import stft


def window_spectrogram(
    audio, start_s, width_s=3.0, n_fft=2048, hop_length=256,
    fmin=18000.0, fmax=100000.0, max_time_bins=None
):
    """
    입력: AudioData, 시간 구간[초], FFT 길이/step[샘플], Hz 단위 주파수 범위.
    반환: (레벨[freq,time], 전역 시간축[s], 주파수축[Hz]).
    """
    if not 0 < hop_length <= n_fft:
        raise ValueError("0 < hop_length <= n_fft 조건이 필요합니다.")
    if width_s <= 0:
        raise ValueError("width_s must be positive.")

    start_sample = max(0, round(start_s * audio.sr))
    stop_sample = min(len(audio.samples), round((start_s + width_s) * audio.sr))
    # 메모리맵 지원 AudioData의 부분 읽기 함수만 사용.
    signal = audio.slice_float(start_sample, stop_sample)

    if len(signal) < n_fft:
        return np.empty((0, 0)), np.array([]), np.array([])

    freqs, local_times, spectrum = stft(
        signal,
        fs=audio.sr,
        nperseg=n_fft,
        noverlap=n_fft - hop_length,
        window="hann",
        boundary=None,
        padded=False,
    )

    keep = (freqs >= fmin) & (freqs <= min(fmax, audio.sr / 2))
    if not keep.any():
        return np.empty((0, 0)), np.array([]), np.array([])

    # 상대 레벨이다. 전압/압력 캘리브레이션이 없으므로 dB SPL 표기 금지.
    level_db = 20 * np.log10(np.maximum(np.abs(spectrum[keep]), 1e-12))
    times_s = local_times + start_sample / audio.sr
    freqs_hz = freqs[keep]

    if max_time_bins is not None and len(times_s) > max_time_bins:
        # GUI 화면 픽셀 수보다 훨씬 많은 컬럼은 downsample하여 렌더링 비용 절감.
        # 단순 subsampling은 화면상 좁은 이벤트를 놓칠 수 있어
        # 후속 개선 시 peak pooling 또는 multiresolution cache로 교체.
        step = int(np.ceil(len(times_s) / max_time_bins))
        times_s = times_s[::step]
        level_db = level_db[:, ::step]

    return level_db, times_s, freqs_hz


def event_level(audio, event, n_fft=1024, hop_length=256):
    """주어진 USV 구간의 대표 상대 레벨; 검증 전 임시 특징값."""
    # 라벨에 USV 주파수 범위가 있으면 그 대역만 사용한다.
    # 없으면 default USV 대역(18kHz 이상)을 사용하며 제한 사항을 결과에서 명시.
    low_hz = event.fmin_hz if event.fmin_hz is not None else 18000.0
    high_hz = event.fmax_hz if event.fmax_hz is not None else audio.sr / 2
    level, _, _ = window_spectrogram(
        audio, event.onset_s, event.offset_s - event.onset_s,
        n_fft=n_fft, hop_length=hop_length,
        fmin=low_hz, fmax=high_hz,
    )
    valid = level[np.isfinite(level)]
    if valid.size == 0:
        return float("nan")

    # 상위 25% 스펙트럼 픽셀의 median을 선택하는 경험적 특징 추출 방식.
    # 샘플 수가 짧거나 배경 잡음이 많은 경우 ground-truth 검증이 필요.
    threshold = np.percentile(valid, 75)
    return float(np.median(valid[valid >= threshold]))

# 주의: 여러 채널의 레벨을 직접 비교하려면 하드웨어별 감도/gain 보정이 필요.
# 각 WAV의 실제 녹음 시작 시각이 다르면 별도 시간 offset 정렬도 필요.