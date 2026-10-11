# [executor/result_viewer.py] 결과 텍스트와 CSV 저장
# 음성 이벤트 하나당 한 행. source mouse_id가 없으면 미할당 또는 ratio-only 상태.
# CSV는 다른 연구자가 검증·후처리할 수 있도록 판단 근거와 점수도 기록한다.
import csv
from pathlib import Path


def result_records(results, channel_subjects=None):
    """학술 보고용 결과 행. 보정 기각을 baseline 채널로 덮어쓰지 않는다."""
    channel_subjects = channel_subjects or {}
    for r in results:
        subject = (channel_subjects.get(r.channel_id) if r.method == "ratio-only"
                   else r.mouse_id)
        subject = subject or "unassigned"
        row = {
            "onset_s": float(r.event.onset_s),
            "offset_s": float(r.event.offset_s),
            "fmin_hz": (r.event.fmin_hz if getattr(r.event, "fmin_hz", None) is not None else ""),
            "fmax_hz": (r.event.fmax_hz if getattr(r.event, "fmax_hz", None) is not None else ""),
            "channel_id": r.channel_id or "",
            "subject_id": subject,
            "mouse_id": r.mouse_id or "",
            "method": r.method,
            "margin_db": float(r.margin_db),
            "reason": r.reason,
        }
        for ch, value in r.levels_db.items():
            row[f"level_db_{ch}"] = float(value)
        for mouse, value in r.candidate_errors_db.items():
            row[f"error_db_{mouse}"] = float(value)
        yield row


def format_results(results):
    """GUI 표시용: 입력 시간 구간 / 채널 / 개체 / 판단 방법."""
    lines = []
    for row in result_records(results):
        cid = row["channel_id"] or "unassigned"
        mid = row["mouse_id"] or "unknown"
        lines.append(
            f"{row['onset_s']:.3f}–{row['offset_s']:.3f}s | "
            f"channel={cid} | mouse={mid} | {row['method']} | {row['reason']}"
        )
    return "\n".join(lines)


def export_results_csv(path, results, channel_subjects=None):
    """모든 분석 모드의 발성 구간·ID·판정 근거를 CSV로 저장.

    results가 리스트면 단일 분석 모드, dict면 {"ratio": [...], ...} 지원.
    """
    if isinstance(results, dict):
        records = []
        for mode, rows in results.items():
            for row in result_records(rows, channel_subjects):
                records.append({"analysis_mode": mode, **row})
    else:
        records = list(result_records(results, channel_subjects))
    if not records:
        raise ValueError("There are no assignment results to export.")
    fields = list(dict.fromkeys(key for row in records for key in row))
    output = Path(path)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return output
