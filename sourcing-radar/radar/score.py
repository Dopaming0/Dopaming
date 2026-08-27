"""아이템 채점 — 100점 만점, 75점 미만은 발주하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Thresholds
from .metrics import TrendStats

WEIGHTS = {
    "competition": 25,
    "trend": 15,
    "shoppable": 15,
    "margin": 20,
    "logistics": 10,
    "cert": 10,
    "expansion": 5,
}


@dataclass
class Score:
    total: int
    parts: dict[str, int] = field(default_factory=dict)
    verdict: str = ""
    reasons: list[str] = field(default_factory=list)


def _lerp(value: float, lo: float, hi: float, max_points: int) -> int:
    """lo에서 만점, hi에서 0점. 그 사이는 선형."""
    if value <= lo:
        return max_points
    if value >= hi:
        return 0
    return round(max_points * (hi - value) / (hi - lo))


def score_competition(comp: float | None, th: Thresholds) -> tuple[int, str]:
    if comp is None:
        return 0, "검색량이 없어 경쟁강도를 계산할 수 없음"
    pts = _lerp(comp, th.comp_excellent, th.comp_good * 3, WEIGHTS["competition"])
    if comp <= th.comp_excellent:
        note = f"경쟁강도 {comp:.2f} — 우수, 광고 없이 진입 가능"
    elif comp <= th.comp_max:
        note = f"경쟁강도 {comp:.2f} — 양호, 리스팅 품질 승부"
    elif comp <= th.comp_good:
        note = f"경쟁강도 {comp:.2f} — 임계 초과, 롱테일로 우회 권장"
    else:
        note = f"경쟁강도 {comp:.2f} — 공급 과잉, 무광고로는 불가"
    return pts, note


def score_trend(stats: TrendStats, th: Thresholds) -> tuple[int, str]:
    if stats.yoy is None:
        return round(WEIGHTS["trend"] * 0.4), "12개월 이전 데이터가 없어 추세 판정 보류"
    if stats.yoy >= th.yoy_growth:
        return WEIGHTS["trend"], f"전년비 {stats.yoy:+.0%} — 성장"
    if stats.yoy >= th.yoy_flat:
        return round(WEIGHTS["trend"] * 0.7), f"전년비 {stats.yoy:+.0%} — 보합"
    if stats.yoy >= th.yoy_flat * 2:
        return round(WEIGHTS["trend"] * 0.25), f"전년비 {stats.yoy:+.0%} — 하락"
    return 0, f"전년비 {stats.yoy:+.0%} — 명확한 하락, 진입 부적합"


def score_margin(net_margin: float | None, th_floor: float, th_target: float) -> tuple[int, str]:
    if net_margin is None:
        return 0, "원가 미입력 — 마진 계산 불가"
    if net_margin >= th_target:
        return WEIGHTS["margin"], f"순마진 {net_margin:.0%} — 목표 달성"
    if net_margin >= th_floor:
        span = th_target - th_floor
        ratio = (net_margin - th_floor) / span if span else 1.0
        return round(WEIGHTS["margin"] * (0.5 + 0.5 * ratio)), f"순마진 {net_margin:.0%} — 하한 통과"
    return 0, f"순마진 {net_margin:.0%} — 하한 {th_floor:.0%} 미달, 탈락"


def score_item(
    comp: float | None,
    stats: TrendStats,
    net_margin: float | None,
    th: Thresholds,
    floor_margin: float,
    target_margin: float,
    shoppable: int = 10,
    logistics: int = 7,
    cert: int = 7,
    expansion: int = 3,
) -> Score:
    parts: dict[str, int] = {}
    reasons: list[str] = []

    for key, (pts, note) in {
        "competition": score_competition(comp, th),
        "trend": score_trend(stats, th),
        "margin": score_margin(net_margin, floor_margin, target_margin),
    }.items():
        parts[key] = pts
        reasons.append(note)

    parts["shoppable"] = min(WEIGHTS["shoppable"], max(0, round(shoppable * 1.5)))
    parts["logistics"] = min(WEIGHTS["logistics"], max(0, logistics))
    parts["cert"] = min(WEIGHTS["cert"], max(0, cert))
    parts["expansion"] = min(WEIGHTS["expansion"], max(0, expansion))

    total = sum(parts.values())

    # 하드 게이트 — 하나라도 걸리면 점수와 무관하게 탈락
    blockers = []
    if comp is not None and comp > th.comp_max:
        blockers.append(f"경쟁강도 {comp:.2f} > {th.comp_max}")
    if net_margin is not None and net_margin < floor_margin:
        blockers.append(f"순마진 {net_margin:.0%} < {floor_margin:.0%}")

    if blockers:
        verdict = "탈락"
        reasons.append("하드 게이트: " + ", ".join(blockers))
    elif total >= 75:
        verdict = "발주 검토"
    elif total >= 60:
        verdict = "보류"
    else:
        verdict = "탈락"

    return Score(total=total, parts=parts, verdict=verdict, reasons=reasons)
