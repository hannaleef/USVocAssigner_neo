# [vocassign/ratio_calc.py] calibrated channel level과 log-ratio
# 캘리브레이션 bias: observed_db = true_relative_db + bias_db
# 따라서 corrected_db = observed_db - bias_db.
# 필수: 채널별 mic sensitivity/gain/frequency-response를 검증한 보정값.
# 물리적 dB SPL 변환은 제공하지 않으므로 ratio-only는 상대 진폭 비교 기준선.
import numpy as np


def db_difference(level_a_db, level_b_db):
    """동일한 기준을 가진 로그 진폭 레벨의 차이[dB]."""
    return float(level_a_db) - float(level_b_db)


def correct_channel_levels(levels_db: dict, recording_bias_db: dict | None = None):
    """각 채널 측정 레벨에서 독립 장비 bias를 제거한다."""
    bias = recording_bias_db or {}
    return {channel: float(value) - float(bias.get(channel, 0.0))
            for channel, value in levels_db.items()}


def rank_levels(levels_db: dict):
    """유효 채널 목록과 최고-차점 차이 반환. 미결정은 NaN."""
    ranked = sorted(
        [(cid, float(db)) for cid, db in levels_db.items() if np.isfinite(db)],
        key=lambda pair: pair[1], reverse=True
    )
    margin = ranked[0][1] - ranked[1][1] if len(ranked) >= 2 else float("nan")
    return ranked, float(margin)
