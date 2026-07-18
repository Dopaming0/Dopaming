# K-뷰티 트렌드 리더 & 신제품 기획 에이전트

대한민국 화장품 시장의 트렌드 신호를 빠르게 수집·분류·분석하고, 근거가 붙은 **신제품 기획서**를 자동으로 산출하는 멀티 스테이지 에이전트입니다.

## 1. 목표

- **입력**: 원시 트렌드 신호(리뷰·랭킹·SNS 게시물·기사 등 JSONL) + 선택적 기획 브리프(예: "20대 타깃 선케어")
- **출력**: 신제품 기획서 1건 — 컨셉, 타깃, 핵심 성분/제형, 포지셔닝, 가격대, 네이밍 후보, 리스크(규제 포함), 출시 로드맵. 모든 주장에 근거(트렌드 데이터·검색 출처) 인용.
- **주기**: 주 1회 실행을 기본으로 설계 (트렌드 사이클이 빠른 K-뷰티 특성상 주 단위가 적절)

## 2. 아키텍처

```
[Stage 0] 신호 수집 (크롤러/수동 익스포트 → signals.jsonl)
    │
[Stage 1] 대량 분류  ──────────  Haiku 4.5 + Batch API (50% 할인)
    │   리뷰/게시물 수천 건 → {카테고리, 성분, 클레임, 감성} 구조화
    │   → 집계 통계 (급상승 성분/카테고리/클레임)
    │
[Stage 2] 렌즈별 심층 분석 ────  Opus 4.8 × 4 (병렬)
    │   ① 성분 트렌드  ② 카테고리·제형  ③ 소비자 니즈·언어  ④ 경쟁사 신제품
    │
[Stage 3] 종합·기획 ──────────  Fable 5 (오케스트레이터, 단일 장기 턴)
        web_search/web_fetch 서버 툴로 근거 검증·공백 보완
        → 신제품 기획서 산출 (스트리밍)
```

### 모델 라우팅 (CLAUDE.md 정책 준수)

| 단계 | 작업 성격 | 모델 | 이유 |
|---|---|---|---|
| Stage 1 | 대량 저난이도 분류 | `claude-haiku-4-5` + Batch API | 수천 건 × 짧은 출력. 배치 50% 할인으로 최저 비용 |
| Stage 2 | 도메인별 분석·요약 | `claude-opus-4-8` | 일반 분석 작업의 기본값. `thinking: adaptive` + `effort: high` |
| Stage 3 | 장기 자율 종합·검증·기획 | `claude-fable-5` | 잘 명세된 one-shot 종합 + 서버 툴 검증 루프. 비용 2배를 넘는 가치가 있는 단계에만 투입 |

## 3. 데이터 소스 (Stage 0)

크롤러는 이 스켈레톤의 범위 밖이며, `signals.jsonl`(한 줄에 신호 1건)로 주입합니다. 권장 소스:

| 소스 | 신호 유형 | 비고 |
|---|---|---|
| 올리브영 랭킹/리뷰 | 판매·리뷰 트렌드 | 카테고리별 주간 스냅샷 |
| 화해(Hwahae) | 성분 관심도, 리뷰 | 성분 트렌드의 핵심 소스 |
| 네이버 데이터랩/쇼핑인사이트 | 검색량 추이 | 급상승 키워드 |
| Instagram/TikTok/YouTube | 밈·챌린지·신조어 | 해시태그 수집 |
| 뷰티 매체(코스인, 장업신문 등) | 업계 뉴스, 신제품 출시 | Stage 3에서 web_search로도 보완 |
| 수출입 통계(관세청) | 글로벌 K-뷰티 수요 | 분기 단위 |

> 💡 **Claude Code 세션에서 실행할 경우**: 이 리포에 연결된 PlayMCP(카카오 공식 MCP 서버)의
> `NaverSearch-datalab_*`(데이터랩 쇼핑인사이트), `NaverSearch-search_blog/news/shop`(블로그·뉴스·쇼핑 검색),
> `YouTubeData-*`(트렌딩 영상·댓글) 툴을 Stage 0 수집기로 바로 사용할 수 있다.
> 수집 결과를 아래 스키마의 `signals.jsonl`로 저장하면 파이프라인에 그대로 연결된다.

`signals.jsonl` 스키마:

```json
{"id": "hw-001", "source": "hwahae", "date": "2026-07-10", "text": "리뷰/게시물 원문...", "meta": {"category": "선케어", "likes": 231}}
```

## 4. 산출물 스키마 (기획서)

Stage 3이 Markdown으로 산출하는 섹션 구조:

1. **트렌드 요약** — 이번 주기 핵심 트렌드 3~5개, 각각 데이터 근거
2. **기회 정의** — 공백 시장 / 급상승 니즈와 그 크기 추정
3. **제품 컨셉** — 한 줄 컨셉, 타깃 페르소나, 핵심 성분·제형, 사용 경험
4. **포지셔닝·가격** — 경쟁 제품 맵, 권장 가격대, 채널 전략
5. **네이밍 후보** — 3안 + 각각의 톤
6. **리스크** — 화장품법 표시·광고 규제(기능성 표방 등), 트렌드 수명, 원료 수급
7. **출시 로드맵** — 12주 기준 마일스톤

## 5. 파일 구성

```
agents/kbeauty-trend-agent/
├── README.md          # 이 문서
├── requirements.txt
├── config.py          # 모델 ID·공통 상수 (라우팅의 단일 소스)
├── batch_classify.py  # Stage 1: Haiku 배치 분류 + 집계
├── subagents.py       # Stage 2: Opus 4.8 렌즈별 병렬 분석
├── orchestrator.py    # Stage 3: Fable 5 종합·기획 (스트리밍)
└── main.py            # 파이프라인 엔트리포인트 (CLI)
```

## 6. 실행

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

python main.py --signals signals.jsonl --brief "20대 타깃 여름 선케어" --out report.md
```

- `--brief`는 선택. 생략하면 트렌드 데이터에서 가장 큰 기회를 에이전트가 스스로 고릅니다.
- Stage 1 배치는 보통 1시간 내 완료(최대 24시간). `--skip-batch`로 이전 집계 결과(`classified.json`)를 재사용할 수 있습니다.

## 7. CLAUDE.md 정책 매핑 (구현 체크리스트)

| 정책 | 구현 위치 |
|---|---|
| Fable 5: `thinking` 파라미터 생략 | `orchestrator.py` — thinking 필드 없음, 깊이는 `output_config.effort`로 제어 |
| Fable 5: refusal 폴백 기본 포함 | `orchestrator.py` — `betas=["server-side-fallback-2026-06-01"]` + `fallbacks=[{"model": "claude-opus-4-8"}]`, `stop_reason == "refusal"` 확인 후 content 접근 |
| Fable 5: 긴 턴 대비 | `orchestrator.py` — 스트리밍 + `max_tokens=64000` + 진행 출력 |
| Fable 5: 덜 지시적인 프롬프트 | 시스템 프롬프트는 목표·제약·산출물 스키마만 명시, 단계 절차 나열 없음. 작업 명세는 첫 턴에 일괄 전달 |
| Opus 4.8: `thinking: adaptive` 명시 | `subagents.py` |
| Opus 4.8: temperature/top_p/top_k/prefill 금지 | 미사용 |
| 대량 배치 → Haiku + Batch API | `batch_classify.py` |
| 프롬프트 캐싱 (고정 시스템 프롬프트 + `cache_control`) | 시스템 프롬프트에 타임스탬프 등 동적 값 없음. 날짜·데이터는 user 턴에 주입 |
| `task_budget`으로 예산 인지 | `orchestrator.py` — `task-budgets-2026-03-13` 베타, 120K 토큰 |
| 16K 초과 출력은 스트리밍 | Stage 3만 64K 스트리밍, Stage 1·2는 비스트리밍 16K 이하 |

## 8. 비용 추정 (주 1회, 신호 5,000건 기준)

| 단계 | 모델 | 대략 토큰 | 대략 비용 |
|---|---|---|---|
| Stage 1 | Haiku 배치 (in $0.5/out $2.5 per 1M, 배치 할인 반영) | in ~2.5M / out ~0.5M | ~$2.5 |
| Stage 2 | Opus 4.8 × 4 | in ~200K / out ~40K | ~$2 |
| Stage 3 | Fable 5 | in ~150K(검색 포함) / out ~30K | ~$3 |
| **합계** | | | **주 ~$8 내외** |

## 9. 로드맵

- [ ] Stage 0 수집기 자동화 (올리브영/화해/네이버 데이터랩 커넥터)
- [ ] 주간 스케줄 실행 + 기획서 아카이브(트렌드 시계열 비교)
- [ ] 이전 주기 기획서를 메모리로 제공 → 트렌드 변화 감지 정확도 향상
- [ ] 기획서 품질 루브릭 기반 자동 평가 (별도 Opus 4.8 grader)
