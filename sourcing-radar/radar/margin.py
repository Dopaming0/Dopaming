"""마진 역산 — 판매가를 감으로 정하지 않기 위한 계산."""
from __future__ import annotations

from dataclasses import dataclass

from .config import MarginPolicy


class UnreachableMargin(ValueError):
    """비용 비율의 합이 1을 넘어 어떤 판매가로도 목표 마진에 닿지 못하는 경우."""


@dataclass
class MarginResult:
    price: int              # 목표 마진을 만족하는 판매가
    unit_cost: int          # 원가 + 물류 + 부대비용
    variable_rate: float    # 판매가에 비례해 빠지는 비율의 합
    net_margin: float       # 그 판매가에서의 실제 순마진
    profit_per_unit: int
    breakeven_price: int    # 순마진 0이 되는 판매가


def _variable_rate(p: MarginPolicy) -> float:
    return p.commission_rate + p.payment_rate + p.ad_rate + p.return_rate + p.vat_rate


def reverse_price(unit_cost: int, policy: MarginPolicy, target_margin: float | None = None) -> MarginResult:
    """판매가 = (원가 + 고정비) ÷ (1 − 변동비율 − 목표마진율).

    감으로 가격을 정한 뒤 마진을 확인하는 게 아니라, 목표 마진을 먼저 박고
    판매가를 역산한다. 나온 가격이 시장 상단을 넘으면 그 아이템은 거기서 끝.
    """
    target = policy.target_margin if target_margin is None else target_margin
    fixed = unit_cost + policy.shipping_cost + policy.fulfillment_cost
    var = _variable_rate(policy)

    denom = 1.0 - var - target
    if denom <= 0:
        raise UnreachableMargin(
            f"변동비 {var:.1%} + 목표마진 {target:.1%} 가 100%를 넘습니다. "
            "목표 마진을 낮추거나 수수료·광고비 가정을 줄이세요."
        )

    price = fixed / denom
    be_denom = 1.0 - var
    breakeven = fixed / be_denom if be_denom > 0 else 0.0

    profit = price * (1.0 - var) - fixed
    return MarginResult(
        price=round(price),
        unit_cost=fixed,
        variable_rate=var,
        net_margin=(profit / price) if price else 0.0,
        profit_per_unit=round(profit),
        breakeven_price=round(breakeven),
    )


def margin_at_price(price: int, unit_cost: int, policy: MarginPolicy) -> float:
    """이미 정해진 판매가에서의 실제 순마진율."""
    if price <= 0:
        return 0.0
    fixed = unit_cost + policy.shipping_cost + policy.fulfillment_cost
    profit = price * (1.0 - _variable_rate(policy)) - fixed
    return profit / price
