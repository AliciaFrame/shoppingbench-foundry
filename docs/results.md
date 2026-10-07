# Results

## Methodology

The published development comparison uses:

- 30 leakage-safe cases per task;
- three rollouts per case;
- 90 episodes per stage;
- one deterministic canonical grader for base, optimized, and RFT models;
- explicit zeroes for runtime errors and nontermination;
- mean score, latency, and total tokens reported together.

Repeated rollouts are paired by case. Comparison receipts include
case-clustered confidence intervals rather than treating all 90 episodes as
independent observations.

## Base, Agent Optimized, and RFT

| Task | Stage | Mean score | Latency | Tokens | Published decision |
|---|---|---:|---:|---:|---|
| Product | Base | 0.7529 | 31.80s | 16,227 | |
|  | Agent Optimized | **0.9408** | **16.22s** | **13,948** | **Selected** |
| Shop | Base | 0.7957 | 51.31s | **14,821** | |
|  | Agent Optimized | **0.9371** | **25.56s** | 33,799 | **Selected** |
| Voucher | Base | 0.8547 | 61.75s | **20,892** | |
|  | Agent Optimized | 0.8606 | 35.17s | 30,897 | |
|  | RFT Step 10 | **0.9248** | **23.10s** | 29,610 | **Selected** |
| Catalog Web | Base | 0.6911 | 99.74s | 77,709 | |
|  | Agent Optimized | **0.7286** | 100.97s | 97,156 | |
|  | Hardened v5 RFT final | 0.7175 | **48.75s** | **37,278** | **Selected hardened RFT** |
| Live Web Search | Base | 0.7636 | **13.77s** | **13,586** | |
|  | Agent Optimized | **0.9047** | 15.23s | 17,779 | **Selected** |
|  | RFT | WIP | WIP | WIP | Not published |

## Final gains

| Task | Score gain vs base | Latency change | Token change |
|---|---:|---:|---:|
| Product | **+0.1880** | **-49.0%** | **-14.0%** |
| Shop | **+0.1414** | **-50.2%** | +128.0% |
| Voucher | **+0.0700** | **-62.6%** | +41.7% |
| Catalog Web | **+0.0264** | **-51.1%** | **-52.0%** |
| Live Web Search | **+0.1411** | +10.6% | +30.9% |

## Interpretation

**Product** improved across all three operating metrics. Agent Optimizer raised
mean score by 0.1880 while nearly halving latency and reducing token use.

**Shop** gained 0.1414 score and halved latency. Its optimized instructions and
tool policy use more context, so token use increased despite faster execution.

**Voucher** required both stages. Agent Optimizer primarily hardened protocol
completion; RFT Step 10 then produced the retained quality and latency result.

**Catalog Web** uses the strict v5 clue, grounding, protocol, and product gates.
The selected RFT checkpoint trades 0.0111 canonical score versus the optimized
agent for substantially stronger protocol completion and roughly half the
latency and token cost. Its dedicated v5 reward mean is 0.6661 with 54/90
episodes passing the 0.90 threshold.

**Live Web Search** is a separate internet-enabled lineage. The retained Agent
Optimizer configuration raised mean score by 0.1411 and strict success from
13/90 to 78/90. Live Web Search RFT remains work in progress and is not part of
the published RFT workflow.

## Evidence

- Base receipts: `evals/results/baseline/`
- Optimized receipts: `evals/results/optimized/`
- Voucher RFT receipts: `evals/results/checkpoints/voucher/`
- Catalog Web v5 RFT receipts: `evals/results/checkpoints/web-v5/`
- Paired comparisons: `evals/results/comparisons/`
- Machine-readable release summary: `evals/results/summary.json`
- Voucher and Catalog Web job receipts: `rft/jobs/`
