"""Stage 1 — 원시 트렌드 신호 대량 분류.

Haiku 4.5 + Message Batches API(50% 할인)로 수천 건의 리뷰/게시물을
{카테고리, 성분, 클레임, 감성} 구조로 분류하고 집계 통계를 만든다.
"""

import json
import time
from collections import Counter

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from config import BATCH_MODEL

CLASSIFY_SYSTEM = (
    "당신은 K-뷰티 시장 분석가다. 주어진 소비자 신호(리뷰·게시물·기사)를 "
    "요청된 JSON 스키마로 분류한다. 성분·클레임은 원문에 실제로 언급된 것만 담는다."
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "product_type": {
            "type": "string",
            "enum": ["skincare", "makeup", "hybrid", "other"],
            "description": "제품군. 스킨케어 효능을 겸한 색조(톤업 선쿠션 등)는 hybrid",
        },
        "category": {
            "type": "string",
            "description": "제품 카테고리 (예: 선케어, 립, 베이스, 스킨케어-토너)",
        },
        "ingredients": {"type": "array", "items": {"type": "string"}},
        "claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": "소비자가 언급한 효능·속성 (예: 속건조, 톤업, 무기자차)",
        },
        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "trend_signal": {
            "type": "string",
            "enum": ["rising", "steady", "declining", "unknown"],
            "description": "원문 맥락상 이 주제가 뜨는 중인지에 대한 판단",
        },
    },
    "required": ["product_type", "category", "ingredients", "claims", "sentiment", "trend_signal"],
    "additionalProperties": False,
}


def load_signals(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def classify_signals(client: anthropic.Anthropic, signals: list[dict]) -> dict[str, dict]:
    """신호 전체를 배치로 분류하고 {signal_id: 분류결과}를 반환한다."""
    requests = [
        Request(
            custom_id=sig["id"],
            params=MessageCreateParamsNonStreaming(
                model=BATCH_MODEL,
                max_tokens=512,
                system=CLASSIFY_SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
                messages=[
                    {
                        "role": "user",
                        "content": f"소스: {sig['source']} / 날짜: {sig['date']}\n\n{sig['text']}",
                    }
                ],
            ),
        )
        for sig in signals
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"[stage1] batch {batch.id} created ({len(requests)} requests)")

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"[stage1] processing... ({batch.request_counts.processing} left)")
        time.sleep(60)

    results: dict[str, dict] = {}
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            continue
        msg = result.result.message
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text:
            results[result.custom_id] = json.loads(text)
    print(f"[stage1] classified {len(results)}/{len(signals)}")
    return results


def aggregate(classified: dict[str, dict], signals: list[dict]) -> dict:
    """분류 결과를 제품군·플랫폼 관점의 집계 통계로 변환한다.

    - 제품군별(skincare/makeup) 분리 집계. hybrid는 양쪽에 모두 반영.
    - 플랫폼 가중치(PLATFORM_WEIGHTS) 적용 — 스킨케어는 화해, 색조는 지그재그 신호를 높게 침.
    - 교차 검증: 같은 키워드가 2개 이상 플랫폼에서 동시에 rising이면 강한 신호로 표시.
    - 확산 단계: 선행 플랫폼(지그재그/무신사)에만 있으면 early, 올리브영까지 번졌으면
      diffusing, 올리브영에만 있으면 mainstream(성숙 국면).
    """
    from sources import DEFAULT_WEIGHT, LEADING_SOURCES, MASS_SOURCES, PLATFORM_WEIGHTS

    source_by_id = {s["id"]: s.get("source", "unknown") for s in signals}
    product_types = ("skincare", "makeup")
    stats = {
        pt: {
            "categories": Counter(),
            "rising_categories": Counter(),
            "ingredients": Counter(),
            "claims": Counter(),
            "pain_points": Counter(),
            "rising_platforms": {},  # keyword -> set(platform)
        }
        for pt in product_types
    }

    for sig_id, item in classified.items():
        source = source_by_id.get(sig_id, "unknown")
        targets = product_types if item["product_type"] == "hybrid" else (item["product_type"],)
        for pt in targets:
            if pt not in stats:
                continue
            weight = PLATFORM_WEIGHTS[pt].get(source, DEFAULT_WEIGHT)
            bucket = stats[pt]
            bucket["categories"][item["category"]] += weight
            for ing in item["ingredients"]:
                bucket["ingredients"][ing] += weight
            for claim in item["claims"]:
                bucket["claims"][claim] += weight
                if item["sentiment"] == "negative":
                    bucket["pain_points"][claim] += weight
            if item["trend_signal"] == "rising":
                bucket["rising_categories"][item["category"]] += weight
                for kw in [item["category"], *item["ingredients"]]:
                    bucket["rising_platforms"].setdefault(kw, set()).add(source)

    def diffusion_stage(platforms: set[str]) -> str:
        leading = bool(platforms & LEADING_SOURCES)
        mass = bool(platforms & MASS_SOURCES)
        if leading and mass:
            return "diffusing"
        if leading:
            return "early"
        if mass:
            return "mainstream"
        return "watch"  # 화해·보조 소스에서만 관측 — 판매 신호로 아직 확인 안 됨

    result: dict = {"total_signals": len(classified)}
    for pt in product_types:
        bucket = stats[pt]
        rising = bucket["rising_platforms"]
        result[pt] = {
            "top_categories": [(k, round(v, 1)) for k, v in bucket["categories"].most_common(20)],
            "rising_categories": [(k, round(v, 1)) for k, v in bucket["rising_categories"].most_common(20)],
            "top_ingredients": [(k, round(v, 1)) for k, v in bucket["ingredients"].most_common(30)],
            "top_claims": [(k, round(v, 1)) for k, v in bucket["claims"].most_common(30)],
            "pain_points": [(k, round(v, 1)) for k, v in bucket["pain_points"].most_common(20)],
            "cross_platform_consensus": sorted(
                kw for kw, platforms in rising.items() if len(platforms) >= 2
            ),
            "diffusion": {
                stage: sorted(kw for kw, p in rising.items() if diffusion_stage(p) == stage)
                for stage in ("early", "diffusing", "mainstream", "watch")
            },
        }
    return result
