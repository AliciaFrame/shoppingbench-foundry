# Results

## Methodology

Every published MAI hill-climb development comparison uses:

- MAI-Code-1.1-Flash for every agent and stage;
- 30 leakage-safe development cases per task, three rollouts each
  (90 episodes per stage);
- one deterministic canonical grader for base, optimized, and RFT agents;
- explicit zeroes for runtime errors and nontermination;
- mean score, latency, total tokens, and list-price cost reported together.

Repeated rollouts are paired by case. Comparison receipts report
case-clustered 95% confidence intervals rather than treating all 90 episodes
as independent.

## Base → Agent Optimized → RFT

| Agent | Stage | Score | Latency | Tokens | Cost / 1k episodes | Decision |
|---|---|---:|---:|---:|---:|---|
| Product | Base | 0.7529 | 31.80s | 16,227 | $5.37 | |
|  | Agent Optimized | **0.9408** | **16.22s** | **13,948** | $5.64 | **Selected** |
| Shop | Base | 0.7957 | 51.31s | **14,821** | **$6.49** | |
|  | Agent Optimized | **0.9371** | **25.56s** | 33,799 | $11.98 | **Selected** |
| Voucher | Base | 0.8547 | 61.75s | **20,892** | $9.44 | |
|  | Agent Optimized | 0.8606 | 35.17s | 30,897 | $12.08 | |
|  | RFT Step 10 | **0.9248** | **23.10s** | 29,610 | **$8.90** | **Selected** |
| Web (static) | Base | 0.6911 | 99.74s | 77,709 | $31.71 | |
|  | Agent Optimized | **0.7286** | 100.97s | 97,156 | $38.53 | **Selected** |
|  | RFT final | 0.7175 | **48.75s** | **37,278** | **$13.33** | Token alternative |
| Web (live) | Base | 0.2497 | 39.15s | **24,023** | $16.79 | |
|  | Agent Optimized | 0.8292 | 20.00s | 25,236 | $13.19 | |
|  | RFT final | **0.9025** | **19.80s** | 26,198 | **$12.56** | **Selected** |

Recorded token cost uses list prices of $0.20 input and $1.20 output per million
MAI-Code-1.1-Flash tokens, with all input priced uncached. The Web (live)
web-search lookup step runs on GPT-5.4-mini and is priced at $0.75 / $4.50.

## Interpretation

**Product** improved on every operating metric. Agent Optimizer raised the
score by 0.188 while halving latency and reducing tokens.

**Shop** gained 0.141 and halved latency. The optimized instructions use more
context, so tokens and cost rose despite faster execution.

**Voucher** needed both levers. Agent Optimizer mainly hardened protocol
completion; RFT Step 10 then lifted the score to 0.925 and was the cheapest and
fastest stage. Step 10 was preferred over the final checkpoint because it
completed more episodes strictly.

**Web (static)** uses the strict v5 clue, grounding, protocol, and product
gates. Agent Optimized has the best score. Its RFT checkpoint gives up 0.011
score with lower recorded token consumption and latency. Recorded tokens
and costs exclude unavailable usage from failed episodes.

The [README model comparison](../README.md#cost-comparison-with-gpt-56) compares
these MAI choices with the best complete GPT-5.6 vanilla or agent-optimized
runs, using per-model prices and explicit missing-usage caveats.

**Web (live)** climbs at both steps:

| Step | Score Δ | 95% CI | RFT reward Δ | 95% CI |
|---|---:|---|---:|---|
| Base → Agent Optimized | +0.579 | [+0.437, +0.711] | +0.559 | [+0.415, +0.692] |
| Agent Optimized → RFT | +0.073 | [+0.001, +0.165] | +0.081 | [+0.010, +0.160] |

The base agent rarely finished: 66 of 90 episodes exhausted their tool steps
without recommending a product. Agent Optimizer fixed most of the protocol;
RFT then halved the remaining unfinished episodes (15 → 7), raised exact
products from 74 to 81 of 90, and solved a case (`web-113`) that no earlier
stage solved.
Tokens rose 4% and latency was unchanged.

The RFT reward (`web_search_rft_grading_v6`) combines an LLM judge, which
checks the resolved clue against the reference answer and the evidence, and
checks product claims and answer completeness, with deterministic gates for
the exact product, recommendation, and termination, and a succinctness cost
for extra tool calls and long answers. Its mean rises 0.180 → 0.739 → 0.820.

## Sealed final test

All selection decisions were frozen before the disjoint 50-case final sets
were opened. Each case received one attempt.

| Agent | Configuration | Score | Exact | Latency | Tokens |
|---|---|---:|---:|---:|---:|
| Product | Agent Optimized | **0.9385** | 46/50 | 16.79s | 11,975 |
| Shop | Agent Optimized | **0.9047** | 45/50 | 30.37s | 34,917 |
| Voucher | RFT Step 10 | **0.8625** | 43/50 | 28.88s | 31,349 |
| Web (live) | RFT final | **0.9055** | 45/50 | 20.11s | 26,725 |

Web (live) generalized: its final-test score matches development (0.903) and
its RFT reward is 0.782. The committed Web (static) final-test receipt
(`evals/results/final/web.jsonl`, 0.6305) predates the hardened v5 selection
and is retained as historical evidence, not as an estimate for the current
Web (static) configuration.

## Evidence

- Base receipts: `evals/results/baseline/`
- Agent Optimized receipts: `evals/results/optimized/`
- RFT receipts: `evals/results/checkpoints/`
- Web (live) RFT-reward scores and paired comparisons: `evals/results/scored/`
- Paired canonical comparisons: `evals/results/comparisons/`
- Sealed final-test receipts: `evals/results/final/`
- Machine-readable release summary: `evals/results/summary.json`
- RFT calibration, submission, status, and training curves: `rft/jobs/`
