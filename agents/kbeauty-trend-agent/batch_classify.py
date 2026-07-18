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
    "required": ["category", "ingredients", "claims", "sentiment", "trend_signal"],
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


def aggregate(classified: dict[str, dict]) -> dict:
    """분류 결과를 렌즈 분석·기획 단계가 소비할 집계 통계로 변환한다."""
    categories = Counter()
    rising_categories = Counter()
    ingredients = Counter()
    claims = Counter()
    negative_claims = Counter()

    for item in classified.values():
        categories[item["category"]] += 1
        if item["trend_signal"] == "rising":
            rising_categories[item["category"]] += 1
        ingredients.update(item["ingredients"])
        claims.update(item["claims"])
        if item["sentiment"] == "negative":
            negative_claims.update(item["claims"])

    return {
        "total_signals": len(classified),
        "top_categories": categories.most_common(20),
        "rising_categories": rising_categories.most_common(20),
        "top_ingredients": ingredients.most_common(30),
        "top_claims": claims.most_common(30),
        "pain_points": negative_claims.most_common(20),
    }
