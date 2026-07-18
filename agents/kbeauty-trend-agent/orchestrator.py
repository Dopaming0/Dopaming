"""Stage 3 — 종합·신제품 기획 (Fable 5 오케스트레이터).

Fable 5 규칙 (CLAUDE.md):
- thinking 파라미터 생략 (항상 켜져 있음; 깊이는 output_config.effort로 제어)
- refusal 폴백 기본 포함 (server-side fallback → Opus 4.8), content 접근 전 stop_reason 확인
- 긴 턴 대비: 스트리밍 + 64K max_tokens + 진행 출력
- 프롬프트는 목표·제약·산출물 스키마만 명시 (단계 절차 나열 금지), 명세는 첫 턴에 일괄 전달
- task_budget으로 예산을 모델이 인지하고 스스로 마무리
"""

import json

import anthropic

from config import (
    BETA_SERVER_SIDE_FALLBACK,
    BETA_TASK_BUDGETS,
    FALLBACK_MODEL,
    MAX_TOKENS_STREAMING,
    ORCHESTRATOR_MODEL,
    ORCHESTRATOR_TASK_BUDGET,
)

# 시스템 프롬프트는 고정 (동적 값 삽입 금지 — 프롬프트 캐싱 유지).
# 날짜·데이터·브리프는 전부 user 턴에 주입한다.
ORCHESTRATOR_SYSTEM = """\
당신은 K-뷰티 신제품 기획 총괄이다. 트렌드 분류 통계와 렌즈별 분석 리포트를 받아,
대한민국 화장품 시장에 출시할 신제품 기획서 1건을 Markdown으로 작성한다.

목표: 근거 없는 기획서가 아니라, 모든 핵심 주장에 데이터 근거 또는 검색 출처가 붙은 기획서.

제약:
- 입력 데이터와 상충하거나 근거가 약한 지점은 web_search/web_fetch로 직접 검증·보완한다.
  최근 출시 경쟁 제품과 규제 이슈는 반드시 검색으로 교차 확인한다.
- 화장품법 표시·광고 규제를 준수하는 표현만 제안한다 (의약품적 효능 표방 금지,
  기능성화장품 심사 대상 여부 명시).
- 트렌드의 수명(단기 밈 vs 구조적 변화)을 구분해 리스크에 반영한다.

산출물 (Markdown, 이 구조를 따른다):
1. 트렌드 요약 (핵심 트렌드 3~5개, 각각 근거)
2. 기회 정의 (공백 시장/급상승 니즈, 크기 추정)
3. 제품 컨셉 (한 줄 컨셉, 타깃 페르소나, 핵심 성분·제형, 사용 경험)
4. 포지셔닝·가격 (경쟁 맵, 권장 가격대, 채널 전략)
5. 네이밍 후보 3안
6. 리스크 (규제·트렌드 수명·원료 수급)
7. 출시 로드맵 (12주)
"""


def run_orchestrator(
    client: anthropic.Anthropic,
    aggregated: dict,
    analyses: dict[str, str],
    brief: str | None = None,
    run_date: str | None = None,
) -> str:
    """기획서를 스트리밍 생성해 최종 Markdown을 반환한다."""
    lens_reports = "\n\n".join(
        f"### 렌즈: {lens}\n{report}" for lens, report in analyses.items()
    )
    brief_line = (
        f"기획 브리프: {brief}"
        if brief
        else "기획 브리프: 없음 — 데이터상 가장 큰 기회를 스스로 선택할 것."
    )
    task = (
        f"{brief_line}\n"
        f"실행 기준일: {run_date or '미지정'}\n\n"
        f"## 트렌드 분류 집계\n```json\n{json.dumps(aggregated, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 렌즈별 분석 리포트\n{lens_reports}"
    )

    messages: list[dict] = [{"role": "user", "content": task}]

    while True:
        with client.beta.messages.stream(
            model=ORCHESTRATOR_MODEL,
            max_tokens=MAX_TOKENS_STREAMING,
            betas=[BETA_SERVER_SIDE_FALLBACK, BETA_TASK_BUDGETS],
            fallbacks=[{"model": FALLBACK_MODEL}],
            output_config={
                "effort": "high",
                "task_budget": {"type": "tokens", "total": ORCHESTRATOR_TASK_BUDGET},
            },
            system=[
                {
                    "type": "text",
                    "text": ORCHESTRATOR_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {"type": "web_search_20260209", "name": "web_search"},
                {"type": "web_fetch_20260209", "name": "web_fetch"},
            ],
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            response = stream.get_final_message()

        # content 접근 전에 반드시 refusal 확인 — 최종 refusal은 폴백 체인 전체가 거절한 경우
        if response.stop_reason == "refusal":
            detail = response.stop_details.explanation if response.stop_details else None
            raise RuntimeError(f"요청이 거절되었습니다 (폴백 포함): {detail}")

        # 서버 툴 반복 한도 도달 시 이어서 실행 (추가 user 메시지 없이 재전송)
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        return "".join(b.text for b in response.content if b.type == "text")
