"""API 자격증명과 실행 설정."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

OPENAPI_BASE = "https://openapi.naver.com"
SEARCHAD_BASE = "https://api.searchad.naver.com"


@dataclass
class MarginPolicy:
    """마진 역산에 쓰는 비용 구조. 전부 판매가 대비 비율(수수료)이거나 원 단위(고정비)."""

    commission_rate: float = 0.108      # 플랫폼 판매 수수료
    payment_rate: float = 0.03          # 결제 수수료
    ad_rate: float = 0.05               # 초기 리뷰 확보용 광고 (무광고 전략이어도 0으로 두지 말 것)
    return_rate: float = 0.05           # 반품·교환 충당
    vat_rate: float = 0.10              # 부가세
    shipping_cost: int = 3000           # 건당 택배비 (원)
    fulfillment_cost: int = 0           # 로켓그로스 입출고·보관료 (원). 쓰면 반드시 채울 것
    target_margin: float = 0.35         # 목표 순마진
    floor_margin: float = 0.25          # 이 아래면 탈락


@dataclass
class Thresholds:
    """채점 임계값. 문서의 기준을 그대로 옮긴 것."""

    comp_excellent: float = 0.3
    comp_good: float = 1.0
    comp_max: float = 0.7               # 이 값을 넘으면 무광고 전략에서 탈락
    volume_min: int = 3000              # 월간 총검색수 하한
    products_max: int = 2000            # 상품 수 상한
    yoy_flat: float = -0.15             # 이 위는 보합으로 본다 (전체 검색량 감소 보정)
    yoy_growth: float = 0.10            # 이 위는 진짜 성장
    surge_multiple: float = 3.0         # 최근 3개월 평균 / 직전 12개월 평균
    surge_products_max: int = 500       # 트렌드 진입 창의 상품 수 상한
    sustained_yoy: float = 0.50         # 급등은 아니어도 이만큼 지속 성장하면 진입 창으로 본다


@dataclass
class Config:
    client_id: str = ""
    client_secret: str = ""
    ad_api_key: str = ""
    ad_secret_key: str = ""
    ad_customer_id: str = ""
    openapi_base: str = OPENAPI_BASE
    searchad_base: str = SEARCHAD_BASE
    margin: MarginPolicy = field(default_factory=MarginPolicy)
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def has_openapi(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def has_searchad(self) -> bool:
        return bool(self.ad_api_key and self.ad_secret_key and self.ad_customer_id)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """config.toml → 환경변수 순으로 읽는다. 환경변수가 이긴다."""
        data: dict = {}
        if path:
            p = Path(path)
            if p.exists():
                data = tomllib.loads(p.read_text(encoding="utf-8"))

        naver = data.get("naver", {})
        cfg = cls(
            client_id=os.getenv("NAVER_CLIENT_ID", naver.get("client_id", "")),
            client_secret=os.getenv("NAVER_CLIENT_SECRET", naver.get("client_secret", "")),
            ad_api_key=os.getenv("NAVER_AD_API_KEY", naver.get("ad_api_key", "")),
            ad_secret_key=os.getenv("NAVER_AD_SECRET_KEY", naver.get("ad_secret_key", "")),
            ad_customer_id=str(os.getenv("NAVER_AD_CUSTOMER_ID", naver.get("ad_customer_id", ""))),
            openapi_base=os.getenv("NAVER_OPENAPI_BASE", naver.get("openapi_base", OPENAPI_BASE)),
            searchad_base=os.getenv("NAVER_SEARCHAD_BASE", naver.get("searchad_base", SEARCHAD_BASE)),
        )
        if "margin" in data:
            cfg.margin = MarginPolicy(**data["margin"])
        if "thresholds" in data:
            cfg.thresholds = Thresholds(**data["thresholds"])
        return cfg
