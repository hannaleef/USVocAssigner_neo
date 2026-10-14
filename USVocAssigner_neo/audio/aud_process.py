# [audio/aud_process.py] 다채널 그래프와 ratio-only 분석에 공통으로 사용하는 STFT
# GUI는 각 채널의 짧은 구간(기본 500 ms)만 읽어 스펙트로그램을 계산한다.
# 데이터 단위: 상대 디지털 진폭 -> 20log10(|STFT|); 이는 dB SPL이 아니다.
# 화면에서 표시할 시간 bin 수만 줄이는 옵션(max_time_bins)은
# 분석용 특징 함수(event_level)에는 적용하지 않는다.

import numpy as np
from scipy.signal import stft


def load_frequency_response_csv(path):
    """Strict microphone-response CSV: frequency_hz,response_db.

    response_db describes RELATIVE microphone sensitivity measured at a
    frequency; 0 dB is the experimenter's chosen sensitivity reference.
    It is subtracted from STFT magnitudes. Do not substitute correction_dB,
    voltage sensitivity, dBV/Pa or arbitrary 0-dB normalization silently.
    Interpolation is linear in dB vs Hz with NO extrapolation outside data.
    """
    from pathlib import Path
    import pandas as pd

    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    if "response_db" not in frame.columns or not (
        "frequency_hz" in frame.columns or "frequency_khz" in frame.columns
    ):
        raise ValueError(
            "Response CSV requires response_db plus frequency_hz OR "
            "frequency_khz. response_db = measured relative sensitivity "
            "(not an already inverted correction_db)."
        )
    if "frequency_hz" in frame.columns:
        f = pd.to_numeric(
            frame["frequency_hz"], errors="raise"
        ).to_numpy(float)
    else:
        f = 1000.0 * pd.to_numeric(
            frame["frequency_khz"], errors="raise"
        ).to_numpy(float)
    d = pd.to_numeric(frame["response_db"], errors="raise").to_numpy(float)
    if (f.size < 2 or not np.isfinite(f).all() or not np.isfinite(d).all()
        or np.any(f < 0) or np.any(np.diff(f) <= 0)):
        raise ValueError(
            "Frequency response needs 2+ finite, strictly increasing "
            "non-negative frequency_hz rows, with finite response_db."
        )
    if np.any(np.abs(d) > 120):
        raise ValueError("Response magnitude >120 dB; verify CSV units.")
    return f, d


def response_correction_db(audio, frequencies_hz):
    """Per-bin -response_db, only inside measured frequency range.

    Outside measured frequencies correction is 0 dB, not extrapolated.
    User must inspect the coverage before interpreting corrected amplitudes.
    """
    freqs = np.asarray(frequencies_hz, dtype=float)
    out = np.zeros_like(freqs, dtype=float)
    if (not getattr(audio, "apply_response", False)
        or audio.response_freq_hz is None or audio.response_db is None):
        return out
    source_freq = audio.response_freq_hz
    covered = (freqs >= source_freq[0]) & (freqs <= source_freq[-1])
    out[covered] = -np.interp(
        freqs[covered], source_freq, audio.response_db
    )
    return out


def calibration_band_level_db(audio, start_s, end_s, fmin_hz, fmax_hz):
    """Band-limited calibrated reference level from a SHARED test source.

    Uses only the selected short time interval (maximum 10 s). Excludes
    unlabeled off-band energy. Never calibrate from ordinary mouse USVs
    emitted at different distances/angles.
    """
    if not (0 <= start_s < end_s <= audio.duration_s
            and end_s-start_s <= 10.0):
        raise ValueError("Calibration interval must lie within audio, <=10s.")
    if not (0 < fmin_hz < fmax_hz < audio.sr/2):
        raise ValueError("Calibration frequencies must be below Nyquist.")
    start = int(round(start_s * audio.sr))
    end = int(round(end_s * audio.sr))
    sample = audio.slice_float(start, end)
    if sample.size < 4096:
        raise ValueError("Calibration interval is too short.")
    from scipy.signal import stft as _stft
    frequencies, _, matrix = _stft(
        sample, fs=audio.sr, window="hann",
        nperseg=2048, noverlap=1024,
        boundary=None, padded=False
    )
    mask = (frequencies >= fmin_hz) & (frequencies <= fmax_hz)
    if not mask.any():
        raise ValueError("No STFT bins in calibration frequency band.")
    powers = np.abs(matrix[mask]) ** 2
    corrections = response_correction_db(audio, frequencies[mask])
    powers *= np.power(10.0, corrections[:, None] / 10.0)
    # Median across time rejects occasional transients.
    representative_power = float(np.median(np.sum(powers, axis=0)))
    if not np.isfinite(representative_power) or representative_power < 1e-12:
        raise ValueError("Calibration band is silent or below usable level.")
    return 10.0*np.log10(representative_power)


def window_spectrogram(
    audio, start_s, width_s=0.5, n_fft=2048, hop_length=256,
    fmin=0.0, fmax=150000.0, max_time_bins=None,
    fft_points=None, include_edge_context=False
):
    """원본 샘플의 시간축을 유지한 STFT.

    n_fft: 실제 Hann window 길이(samples)
    hop_length: 인접 프레임 간격(samples)
    fft_points: Zero-padding 후 FFT 크기. None이면 n_fft와 동일.
                주파수 격자만 촘촘해지며 실제 분해능은 향상되지 않는다.
    include_edge_context: 상세 보기에서 창 중앙이 화면 가장자리에도 오도록
                          양쪽 n_fft/2 샘플을 읽은 뒤 결과 시간축만 잘라낸다.
    반환: (상대 dB[freq,time], 절대 시간축[s], 주파수축[Hz]).
    """
    if not 0 < hop_length <= n_fft:
        raise ValueError("0 < hop_length <= n_fft 조건이 필요합니다.")
    if width_s <= 0:
        raise ValueError("width_s must be positive.")
    if fft_points is not None:
        if int(fft_points) != fft_points or int(fft_points) < n_fft:
            raise ValueError("fft_points must be an integer >= n_fft.")
        fft_points = int(fft_points)

    requested_start = max(0, round(start_s * audio.sr))
    requested_stop = min(
        len(audio.samples), round((start_s + width_s) * audio.sr)
    )
    margin = n_fft // 2 if include_edge_context else 0
    start_sample = max(0, requested_start - margin)
    stop_sample = min(len(audio.samples), requested_stop + margin)
    signal = audio.slice_float(start_sample, stop_sample)

    if len(signal) < n_fft:
        return np.empty((0, 0)), np.array([]), np.array([])

    freqs, local_times, spectrum = stft(
        signal,
        fs=audio.sr,
        nperseg=n_fft,
        noverlap=n_fft - hop_length,
        nfft=fft_points,
        window="hann",
        boundary=None,
        padded=False,
    )

    keep = (freqs >= fmin) & (freqs <= min(fmax, audio.sr / 2))
    if not keep.any():
        return np.empty((0, 0)), np.array([]), np.array([])

    # Correct relative dB levels at the STFT bin BEFORE computing event-level
    # maxima. This one function is used by ratio-only, distance/angle,
    # spectrogram display and MP4; keep the original PCM untouched.
    level_db = 20 * np.log10(np.maximum(np.abs(spectrum[keep]), 1e-12))
    level_db += response_correction_db(audio, freqs[keep])[:, None]
    if getattr(audio, "apply_gain", False):
        gain_db = float(getattr(audio, "gain_db", 0.0))
        if not np.isfinite(gain_db):
            raise ValueError("Invalid gain calibration.")
        level_db += gain_db
    # Values are still relative dBFS-like amplitudes, NOT calibrated dB SPL.
    times_s = local_times + start_sample / audio.sr
    freqs_hz = freqs[keep]

    if include_edge_context:
        # 표시하려는 구간에서 창 중앙이 벗어난 프레임만 제외한다.
        # 가장자리에서 발생한 5 ms USV가 짧은 잘린 FFT 창으로 사라지는 것을 방지.
        within = (
            (times_s >= requested_start / audio.sr)
            & (times_s <= requested_stop / audio.sr)
        )
        times_s = times_s[within]
        level_db = level_db[:, within]

    if max_time_bins is not None and len(times_s) > max_time_bins:
        # 레거시 미리보기 옵션만 유지. 상세 표시/정량 분석은 사용하지 않는다.
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

    # 기존 방식(전체 freq×time 픽셀 중 상위 25%의 중앙값)은
    # 좁은 대역의 USV 에너지가 수많은 배경잡음 주파수 bin에 희석되는 버그가 있었다.
    # 같은 구간/동일 n_fft/hop_length에서 프레임마다 가장 강한 주파수 bin을 뽑고,
    # 프레임 간 80번째 백분위수로 대표 발성 레벨을 계산한다.
    # 즉, L_event = P80_time[max_freq(20log10(|STFT|))].
    # 진폭비 결정에 필요한 dB 차이는 L_A - L_B = 20log10(A_A/A_B).
    # STFT 최대 bin 방식은 좁은 대역 USV에 적합한 baseline feature지만
    # 스파이크 잡음이나 서로 다른 마이크 감도에는 추가 보정/검증이 필요하다.
    if level.ndim != 2 or level.shape[1] == 0:
        return float("nan")
    frame_peak_db = np.max(
        np.where(np.isfinite(level), level, -np.inf), axis=0
    )
    frame_peak_db = frame_peak_db[np.isfinite(frame_peak_db)]
    if not frame_peak_db.size:
        return float("nan")
    return float(np.percentile(frame_peak_db, 80))

# 주의: 여러 채널의 레벨을 직접 비교하려면 하드웨어별 감도/gain 보정이 필요.
# 각 WAV의 실제 녹음 시작 시각이 다르면 별도 시간 offset 정렬도 필요.