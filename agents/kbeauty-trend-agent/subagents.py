"""Stage 2 — 렌즈별 심층 분석 (Opus 4.8, 병렬).

집계 통계 + 원시 신호 샘플을 4개 렌즈로 나눠 분석한다.
Opus 4.8 규칙: thinking adaptive 명시, temperature/prefill 미사용, effort=high.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import anthropic

from config import MAX_TOKENS_NON_STREAMING, SUBAGENT_MODEL

SUBAGENT_SYSTEM = (
    "당신은 K-뷰티 시장의 시니어 트렌드 분석가다. 주어진 집계 데이터와 신호 샘플을 "
    "지정된 렌즈로 분석하고, 데이터 근거가 붙은 핵심 발견 3~5개와 신제품 기획에 주는 "
    "시사점을 Markdown으로 정리한다. 데이터에 없는 사실은 추정임을 명시한다."
)

ANALYSIS_LENSES = {
    "ingredient": "성분 트렌드 렌즈: 급상승 성분, 성분 조합, 소비자가 성분을 언급하는 맥락과 기대 효능.",
    "category": "카테고리·제형 렌즈: 뜨는 카테고리와 제형(스틱·밤·패드 등), 카테고리 간 이동(스킨케어→하이브리드 등).",
    "consumer": "소비자 니즈·언어 렌즈: 페인 포인트, 신조어·밈, 구매 결정 요인, 세대별 차이.",
    "competitor": "경쟁·시장 렌즈: 최근 출시 제품과의 중복/공백, 가격대 분포, 채널(올리브영/다이소/해외) 동학.",
}


def _run_lens(client: anthropic.Anthropic, lens: str, aggregated: dict, sample_signals: list[dict]) -> tuple[str, str]:
    task = (
        f"분석 렌즈: {ANALYSIS_LENSES[lens]}\n\n"
        f"## 집계 통계\n```json\n{json.dumps(aggregated, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 원시 신호 샘플\n```json\n{json.dumps(sample_signals, ensure_ascii=False, indent=2)}\n```"
    )
    response = client.messages.create(
        model=SUBAGENT_MODEL,
        max_tokens=MAX_TOKENS_NON_STREAMING,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{"type": "text", "text": SUBAGENT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": task}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return lens, text


def run_analyses(client: anthropic.Anthropic, aggregated: dict, signals: list[dict], sample_size: int = 60) -> dict[str, str]:
    """4개 렌즈 분석을 병렬 실행하고 {lens: 분석 Markdown}을 반환한다."""
    sample = [
        {"source": s["source"], "date": s["date"], "text": s["text"][:500]}
        for s in signals[:sample_size]
    ]
    with ThreadPoolExecutor(max_workers=len(ANALYSIS_LENSES)) as pool:
        futures = [
            pool.submit(_run_lens, client, lens, aggregated, sample)
            for lens in ANALYSIS_LENSES
        ]
        results = dict(f.result() for f in futures)
    for lens in results:
        print(f"[stage2] lens '{lens}' done ({len(results[lens])} chars)")
    return results
