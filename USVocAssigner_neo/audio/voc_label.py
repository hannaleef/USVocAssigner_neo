# [audio/voc_label.py] 여러 USV 라벨의 시간구간 병합
# 전제: 라벨 시간축이 채널 간 동기화되어 있어야 한다.
# 서로 다른 발성 주체의 동시 이벤트를 무조건 합치면 정보가 손실될 수 있다.
# tolerance_s는 레이블 흔들림 보정용 값으로 실험 데이터에서 조정한다.

from .data_load import load_labels, USVEvent

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