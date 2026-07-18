# CLAUDE.md

이 파일은 이 리포지토리에서 Claude 세션이 시작될 때 자동으로 로드되는 프로젝트 지침입니다.

## 프로젝트 개요

- **여덟글자 四柱八字** — 사주명리 상담소 모바일 웹 (세일즈 퍼널 인트로 페이지)
- 별도 빌드 없이 `index.html` 단일 파일로 동작. 모바일 우선(max-width 480px)
- 디자인: 겸재 정선 진경산수화풍(한지·먹·낙관), Pretendard + 고운바탕 + Song Myung
- 실행: `npx http-server -p 8080` 후 모바일 뷰포트로 확인
- 상세 콘셉트·페이지 구성·명식 계산 로직은 `README.md` 참고

## 대화 규칙

- 사용자와의 대화는 **한국어**로 한다. 코드·커밋 메시지는 영어 가능.
- 카피 문구를 수정할 때는 README의 톤(사람 냄새 나는 손글씨 감성, 메인 2줄·서브 1~2줄)을 유지한다.

## AI 모델 사용 정책 (Fable / Opus)

이 프로젝트에서 Claude API 연동 코드를 작성하거나 모델을 추천할 때 아래 정책을 따른다.

### 기본 원칙: Opus가 기본값, Fable은 선별 투입

| 작업 유형 | 모델 | 모델 ID |
|---|---|---|
| 일반 코딩, 요약, 추출, 분류, 채팅, 리뷰 | Opus 4.8 (기본값) | `claude-opus-4-8` |
| 장시간 자율 에이전트, 대형 마이그레이션, 깊은 리서치, 잘 명세된 시스템의 one-shot 구현 | Fable 5 | `claude-fable-5` |
| 대량 저난이도 배치 (Batch API 50% 할인 활용) | Sonnet 5 / Haiku 4.5 | `claude-sonnet-5` / `claude-haiku-4-5` |
| 멀티에이전트 구조 | 오케스트레이터만 Fable 5, 하위 작업은 Opus 4.8 이하 | — |

비용: Fable 5는 $10/$50, Opus 4.8은 $5/$25 (per 1M tokens). 비용 2배 차이를 넘는 가치가 있는 작업에만 Fable을 쓴다.

### Fable 5 코드 작성 시 필수 규칙

1. **`thinking` 파라미터를 생략한다.** thinking은 항상 켜져 있으며 `{type: "disabled"}`나 `budget_tokens`는 400 에러. 사고 깊이는 `output_config.effort`(`low`/`medium`/`high`/`xhigh`/`max`)로 조절.
2. **refusal 폴백을 기본으로 포함한다:**
   ```python
   response = client.beta.messages.create(
       model="claude-fable-5",
       max_tokens=16000,
       betas=["server-side-fallback-2026-06-01"],
       fallbacks=[{"model": "claude-opus-4-8"}],
       messages=[...],
   )
   ```
   `response.content`를 읽기 전에 반드시 `stop_reason == "refusal"`을 먼저 확인한다.
3. **긴 턴에 대비한다.** 어려운 작업은 단일 요청이 수 분(최대 ~15분)까지 걸릴 수 있으므로 스트리밍 + 넉넉한 타임아웃 + 진행 표시 UX를 설계한다.
4. **프롬프트는 덜 지시적으로.** 단계별 절차를 나열하지 말고 목표와 제약만 명시한다. 작업 전체 명세는 첫 턴에 한 번에 준다.

### Opus 4.8 코드 작성 시 필수 규칙

1. `thinking: {type: "adaptive"}`를 명시적으로 설정한다 (생략 시 thinking 꺼짐).
2. `temperature`/`top_p`/`top_k`, `budget_tokens`, assistant prefill은 모두 400 에러 — 사용 금지.
3. effort 기본값: 코딩·에이전트는 `xhigh`, 일반 작업은 `high`.

### 공통 최적화 규칙

- **프롬프트 캐싱**: 시스템 프롬프트는 고정(타임스탬프·UUID 등 동적 값 삽입 금지)하고 `cache_control: {type: "ephemeral"}`을 적용한다. 세션 중간에 모델·툴 목록을 바꾸면 캐시가 무효화되므로, 다른 모델이 필요하면 서브에이전트로 분리한다.
- **긴 에이전트 루프**: `output_config.task_budget`(베타 `task-budgets-2026-03-13`, 최소 20,000 토큰)으로 모델이 예산을 인지하고 스스로 마무리하게 한다.
- **`max_tokens`**: 비스트리밍 ~16000, 스트리밍 ~64000 기본. 16K 초과 출력은 반드시 스트리밍.
- **effort 스윕**: Fable 5는 `low`/`medium`에서도 이전 모델 `max`급 성능이 나오는 경우가 많으므로, 루틴 작업까지 포함해 effort별로 테스트한 뒤 라우팅을 정한다.
