"""데이터 소스 라우팅 — 제품군별 수집 플랫폼, 역할, 가중치의 단일 소스.

제품군별 소스 구성:
- 스킨케어: 올리브영 / 화해 / 무신사뷰티
- 색조:     올리브영 / 무신사뷰티 / 지그재그
"""

SOURCES = {
    "oliveyoung": {
        "name": "올리브영",
        "role": "대중 판매 검증 (규모는 크지만 트렌드 후행)",
        "demographic": "전연령 대중",
        "collect": "카테고리별 주간 랭킹 스냅샷 + 상위 제품 리뷰 샘플링",
    },
    "hwahae": {
        "name": "화해",
        "role": "성분·효능 신뢰 신호 (스킨케어 트렌드의 근거 축)",
        "demographic": "성분 관여도 높은 소비자",
        "collect": "성분 랭킹·카테고리 어워드 + 리뷰 샘플링",
    },
    "musinsa_beauty": {
        "name": "무신사뷰티",
        "role": "1020 얼리어답터 신호 (패션 연계 트렌드 선행)",
        "demographic": "10~20대 남녀",
        "collect": "뷰티 랭킹 스냅샷 + 신규 입점 브랜드 추적",
    },
    "zigzag": {
        "name": "지그재그",
        "role": "1020 여성 얼리어답터 신호 (색조 트렌드 최선행)",
        "demographic": "10~20대 여성",
        "collect": "뷰티 랭킹 스냅샷 + 검색어/기획전 추적",
    },
}

# 제품군 → 사용 소스
CATEGORY_SOURCES = {
    "skincare": ["oliveyoung", "hwahae", "musinsa_beauty"],
    "makeup": ["oliveyoung", "musinsa_beauty", "zigzag"],
}

# 확산 단계 판정용 소스 분류: 선행(얼리어답터) → 대중(검증)
LEADING_SOURCES = {"zigzag", "musinsa_beauty"}
MASS_SOURCES = {"oliveyoung"}

# 집계 가중치 — 각 제품군에서 그 플랫폼 신호를 얼마나 신뢰할지.
# 스킨케어는 성분 신뢰(화해)를, 색조는 1020 선행 신호(지그재그)를 높게 친다.
PLATFORM_WEIGHTS = {
    "skincare": {"oliveyoung": 1.0, "hwahae": 1.2, "musinsa_beauty": 0.8},
    "makeup": {"oliveyoung": 1.0, "musinsa_beauty": 1.1, "zigzag": 1.2},
}
DEFAULT_WEIGHT = 0.5  # 라우팅에 없는 소스(뉴스 등)의 보조 가중치
