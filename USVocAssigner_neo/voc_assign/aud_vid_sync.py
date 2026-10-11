# [vocassign/aud_vid_sync.py] 오디오·영상 타임스탬프 정렬
# 양의 offset_s: 영상 t초와 같은 사건이 오디오에서 t+offset초에 발생.
# 실측 동기 신호로 offset을 정해야 한다. 단순히 파일 길이가 같다는 것은 증거가 아니다.
from dataclasses import dataclass


@dataclass(frozen=True)
class SyncConfig:
    offset_s: float = 0.0
    # 장시간 녹음에서 시간축 편차가 측정되면 drift 비율도 보정할 수 있다.
    # audio_s = video_s*(1+drift) + offset_s
    drift: float = 0.0

    def video_to_audio(self, video_s: float) -> float:
        return float(video_s) * (1.0 + self.drift) + self.offset_s

    def audio_to_video(self, audio_s: float) -> float:
        if 1.0 + self.drift <= 0:
            raise ValueError("Invalid synchronization drift.")
        return (float(audio_s) - self.offset_s) / (1.0 + self.drift)


def video_to_audio_s(video_s, offset_s=0.0):
    return SyncConfig(offset_s).video_to_audio(video_s)


def audio_to_video_s(audio_s, offset_s=0.0):
    return SyncConfig(offset_s).audio_to_video(audio_s)


def audio_sample_at_video(video_s, sr, offset_s=0.0):
    return round(video_to_audio_s(video_s, offset_s) * sr)


def video_frame_at_audio(audio_s, fps, offset_s=0.0):
    return round(audio_to_video_s(audio_s, offset_s) * fps)


def sync_from_start_points(audio_start_s: float, video_start_s: float,
                           drift: float = 0.0) -> SyncConfig:
    """각 Sync 버튼에서 고정한 두 매체의 *동일 사건* 기준점을 정렬한다.

    audio_start_s: 오디오에서 정한 사건의 시각 (초).
    video_start_s: 비디오에서 정한 같은 사건의 시각 (초).
    t_audio = (t_video - video_start_s)*(1 + drift) + audio_start_s
            = t_video*(1 + drift) + offset_s
    버튼을 한쪽만 눌렀을 때는 동기화가 성립하지 않는다.
    참고: 시작점 두 개만으로는 drift를 추정할 수 없으므로 기본값 0.
    """
    import math
    if not all(math.isfinite(v) for v in (audio_start_s, video_start_s, drift)):
        raise ValueError("Synchronization markers must be finite numbers.")
    if audio_start_s < 0 or video_start_s < 0 or 1 + drift <= 0:
        raise ValueError("Invalid synchronization start points or drift.")
    return SyncConfig(
        offset_s=float(audio_start_s) - (1.0 + drift) * float(video_start_s),
        drift=float(drift),
    )


def estimate_offset_from_events(audio_event_s, video_event_s) -> SyncConfig:
    """동일한 사건(TTL/LED/충격 등)의 양쪽 시간에서 offset을 계산."""
    return SyncConfig(offset_s=float(audio_event_s) - float(video_event_s))


def estimate_sync_two_events(audio_events_s, video_events_s) -> SyncConfig:
    """시작/종료의 두 공통 동기 사건으로 drift까지 선형 추정."""
    if len(audio_events_s) != 2 or len(video_events_s) != 2:
        raise ValueError("Two matched event times required in each modality.")
    av0, av1 = map(float, audio_events_s)
    vv0, vv1 = map(float, video_events_s)
    if vv1 <= vv0 or av1 <= av0:
        raise ValueError("Sync events must be strictly increasing.")
    slope = (av1 - av0) / (vv1 - vv0)
    return SyncConfig(offset_s=av0 - slope * vv0, drift=slope - 1.0)

