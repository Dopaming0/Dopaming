"""K-뷰티 트렌드 → 신제품 기획 파이프라인 엔트리포인트.

사용법:
    python main.py --signals signals.jsonl --brief "20대 타깃 여름 선케어" --out report.md
"""

import argparse
import datetime
import json
import sys

import anthropic

from batch_classify import aggregate, classify_signals, load_signals
from orchestrator import run_orchestrator
from subagents import run_analyses

CLASSIFIED_CACHE = "classified.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="K-뷰티 트렌드 신제품 기획 에이전트")
    parser.add_argument("--signals", required=True, help="원시 신호 JSONL 경로")
    parser.add_argument("--brief", default=None, help="선택적 기획 브리프")
    parser.add_argument("--out", default="report.md", help="기획서 출력 경로")
    parser.add_argument(
        "--skip-batch",
        action="store_true",
        help=f"{CLASSIFIED_CACHE}의 이전 분류 결과를 재사용",
    )
    args = parser.parse_args()

    client = anthropic.Anthropic()
    signals = load_signals(args.signals)
    print(f"[stage0] loaded {len(signals)} signals")

    # Stage 1 — Haiku 배치 분류 (완료까지 최대 수십 분)
    if args.skip_batch:
        with open(CLASSIFIED_CACHE, encoding="utf-8") as f:
            classified = json.load(f)
        print(f"[stage1] reused {len(classified)} cached classifications")
    else:
        classified = classify_signals(client, signals)
        with open(CLASSIFIED_CACHE, "w", encoding="utf-8") as f:
            json.dump(classified, f, ensure_ascii=False)

    aggregated = aggregate(classified)

    # Stage 2 — Opus 4.8 렌즈별 병렬 분석
    analyses = run_analyses(client, aggregated, signals)

    # Stage 3 — Fable 5 종합·기획 (스트리밍)
    print("\n[stage3] generating product plan...\n")
    report = run_orchestrator(
        client,
        aggregated,
        analyses,
        brief=args.brief,
        run_date=datetime.date.today().isoformat(),
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n\n[done] report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
