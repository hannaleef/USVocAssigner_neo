# [audio/voc_label.py] 여러 USV 라벨의 시간구간 병합
# 전제: 라벨 시간축이 채널 간 동기화되어 있어야 한다.
# 서로 다른 발성 주체의 동시 이벤트를 무조건 합치면 정보가 손실될 수 있다.
# tolerance_s는 레이블 흔들림 보정용 값으로 실험 데이터에서 조정한다.

from .data_load import load_labels, USVEvent
import numpy as np


def merge_channel_events(per_channel_events, tolerance_s=0.005, min_iou=0.60):
    """N개 마이크의 라벨에서 중복된 발성만 통합한 공통 이벤트 목록.

    각 채널 파일은 같은 녹음 시간축을 사용해야 한다. 채널별로 검출되는
    같은 발성은 onset/offset 오차가 있을 수 있으므로 높은 시간 IoU 또는
    매우 가까운 시작/종료 경계를 기준으로 dedup한다.
    같은 채널 안의 발성 두 개는 절대 하나로 묶지 않는다.
    """
    entries = [
        (channel, event)
        for channel, events in enumerate(per_channel_events)
        for event in events
    ]
    entries.sort(key=lambda x: (x[1].onset_s, x[1].offset_s, x[0]))
    clusters = []

    def overlap_ratio(a, b):
        intersection = max(
            0.0, min(a.offset_s, b.offset_s) - max(a.onset_s, b.onset_s)
        )
        union = max(a.offset_s, b.offset_s) - min(a.onset_s, b.onset_s)
        return intersection / union if union > 0 else 0.0

    for channel, event in entries:
        best_idx, best_score = None, -1.0
        for i, cluster in enumerate(clusters):
            # 같은 마이크에서 별개로 라벨링한 이벤트는 병합 금지.
            if channel in cluster["channels"]:
                continue
            reference = cluster["event"]
            nearby_boundaries = (
                abs(reference.onset_s - event.onset_s) <= tolerance_s
                and abs(reference.offset_s - event.offset_s) <= tolerance_s
            )
            iou = overlap_ratio(reference, event)
            if not (nearby_boundaries or iou >= min_iou):
                continue
            # 서로 다른 주파수 대역의 동시발성은 별개 이벤트로 보존.
            if reference.fmin_hz is not None and event.fmin_hz is not None:
                low = max(reference.fmin_hz, event.fmin_hz)
                high = min(reference.fmax_hz, event.fmax_hz)
                f_union = max(reference.fmax_hz, event.fmax_hz) - min(
                    reference.fmin_hz, event.fmin_hz
                )
                if high <= low or (high-low)/max(f_union, 1e-9) < 0.20:
                    continue
            score = iou + (1.0 if nearby_boundaries else 0.0)
            if score > best_score:
                best_idx, best_score = i, score

        if best_idx is None:
            clusters.append({"event": event, "events": [event],
                             "channels": {channel}})
            continue

        cluster = clusters[best_idx]
        cluster["events"].append(event)
        cluster["channels"].add(channel)
        members = cluster["events"]
        # 마이크별 측정 경계값의 median은 한 채널의 지연/잡음을 줄여준다.
        onset = float(np.median([x.onset_s for x in members]))
        offset = float(np.median([x.offset_s for x in members]))
        valid_frequency = [x for x in members if x.fmin_hz is not None]
        fmin = (float(np.median([x.fmin_hz for x in valid_frequency]))
                if valid_frequency else None)
        fmax = (float(np.median([x.fmax_hz for x in valid_frequency]))
                if valid_frequency else None)
        cluster["event"] = USVEvent(onset, offset, fmin, fmax)

    return sorted((c["event"] for c in clusters), key=lambda e: e.onset_s)


# 매우 가까운 USV 라벨을 하나의 구간으로 묶는다; tolerance 기본값 3ms.
def merge_events(events, tolerance_s=0.003):
    """Optional union of close labels; inspect overlapping simultaneous calls."""
    # 입력이 비어있으면 즉시 빈 결과를 돌려준다.
    if not events: return []
    # 순서가 뒤섞인 CSV 라벨도 시작 시각 기준으로 정렬한다.
    ordered = sorted(events, key=lambda e: e.onset_s)
    merged = [ordered[0]]
    for event in ordered[1:]:
        prev = merged[-1]
        # 겹치거나 근접한 구간을 병합한다. 동시발성까지 합쳐질 위험 주의.
        if event.onset_s <= prev.offset_s + tolerance_s:
            # 시작은 앞 구간, 종료는 더 늦은 종료 시각으로 확장한다.
            merged[-1] = USVEvent(prev.onset_s, max(prev.offset_s, event.offset_s))
        else:
            # 떨어진 이벤트는 새로운 USV 이벤트로 보관한다.
            merged.append(event)
    return merged