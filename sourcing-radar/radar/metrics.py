"""수요·공급 지표 계산 — 경쟁강도, 전년비, 급등 감지."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass
class TrendStats:
    yoy: float | None            # 전년 동월 대비 변화율
    recent3: float               # 최근 3개월 평균 지수
    baseline12: float            # 그 직전 12개월 평균 지수
    surge: float | None          # recent3 / baseline12
    peak_month: int | None       # 지수가 가장 높은 달 (1-12)
    amplitude: float | None      # 최고/최저 — 계절성 크기
    points: int


def competition_index(products: int, volume: int) -> float | None:
    """경쟁강도 = 상품 수 ÷ 월간 총검색 수. 1을 넘으면 공급 과잉."""
    if not volume:
        return None
    return products / volume


def _series(data: list[dict]) -> list[tuple[str, float]]:
    return [(d["period"], float(d["ratio"])) for d in data if d.get("ratio") is not None]


def trend_stats(data: list[dict], drop_last: bool = True) -> TrendStats:
    """데이터랩 시계열에서 지표를 뽑는다.

    drop_last: 조회 시점의 당월은 부분 집계라 낮게 나오므로 기본으로 버린다.
    """
    pts = _series(data)
    if drop_last and len(pts) > 1:
        pts = pts[:-1]
    if not pts:
        return TrendStats(None, 0.0, 0.0, None, None, None, 0)

    values = [v for _, v in pts]

    yoy = None
    if len(pts) >= 13:
        current, year_ago = values[-1], values[-13]
        if year_ago:
            yoy = (current - year_ago) / year_ago

    recent3 = mean(values[-3:]) if len(values) >= 3 else mean(values)
    window = values[-15:-3] if len(values) >= 15 else values[:-3] or values
    baseline12 = mean(window)
    surge = (recent3 / baseline12) if baseline12 else None

    peak_month = None
    if pts:
        peak_period = max(pts, key=lambda p: p[1])[0]
        try:
            peak_month = int(peak_period.split("-")[1])
        except (IndexError, ValueError):
            peak_month = None

    lo, hi = min(values), max(values)
    amplitude = (hi / lo) if lo > 0 else None

    return TrendStats(yoy, recent3, baseline12, surge, peak_month, amplitude, len(values))


def classify_trend(stats: TrendStats, flat_floor: float, growth_floor: float) -> str:
    """전체 검색량이 줄고 있어 −15%까지는 보합으로 읽는다."""
    if stats.yoy is None:
        return "unknown"
    if stats.yoy >= growth_floor:
        return "growth"
    if stats.yoy >= flat_floor:
        return "flat"
    return "decline"


def is_entry_window(
    stats: TrendStats,
    products: int,
    surge_multiple: float,
    products_max: int,
    sustained_yoy: float = 0.50,
) -> bool:
    """진입 창: 수요가 오르는데 아직 공급이 비어 있는 상태.

    두 가지 모양을 모두 잡는다.
      1) 짧고 날카로운 급등 — 최근 3개월 평균이 직전 12개월 평균의 N배 (유행성)
      2) 완만하지만 강한 지속 성장 — 전년비가 임계 이상 (피클볼형)

    2번이 필요한 이유: 12개월에 걸쳐 꾸준히 오르면 직전 12개월 평균 자체가
    같이 올라가 배수가 눌린다. 배수만 보면 가장 좋은 후보를 놓친다.
    """
    if products >= products_max:
        return False
    if stats.surge is not None and stats.surge >= surge_multiple:
        return True
    if stats.yoy is not None and stats.yoy >= sustained_yoy:
        return True
    return False
