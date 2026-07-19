"""모델 라우팅 단일 소스 — CLAUDE.md의 AI 모델 사용 정책을 따른다."""

# Stage 3 오케스트레이터: 장기 자율 종합·기획에만 Fable을 투입
ORCHESTRATOR_MODEL = "claude-fable-5"

# Fable refusal 시 서버 사이드 폴백 대상
FALLBACK_MODEL = "claude-opus-4-8"

# Stage 2 렌즈별 분석: 일반 분석 작업의 기본값
SUBAGENT_MODEL = "claude-opus-4-8"

# Stage 1 대량 저난이도 분류: Batch API 50% 할인 활용
BATCH_MODEL = "claude-haiku-4-5"

# 비스트리밍 기본 출력 한도 (16K 초과 출력은 반드시 스트리밍)
MAX_TOKENS_NON_STREAMING = 16000
MAX_TOKENS_STREAMING = 64000

# Stage 3 태스크 예산 (task-budgets 베타, 최소 20,000)
ORCHESTRATOR_TASK_BUDGET = 120_000

BETA_SERVER_SIDE_FALLBACK = "server-side-fallback-2026-06-01"
BETA_TASK_BUDGETS = "task-budgets-2026-03-13"
