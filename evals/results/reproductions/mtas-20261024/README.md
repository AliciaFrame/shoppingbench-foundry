# Qwen3.8 development baseline: mtas-20261024

This archive contains **450 actual evaluation episodes** from the user-directed
Qwen3.8-27B adaptation: five tasks, 30 development cases each, three rollouts per
case. Inference finished on October 24, 2026 at 18:43:51 Pacific
(`2026-10-25T01:43:51Z`). This is not an exact MAI reproduction.

| Task | Published MAI baseline | Qwen baseline | Strict successes | Mean latency | Runtime errors |
|---|---:|---:|---:|---:|---:|
| Product | 0.7529 | 0.854907 | 7/90 | 36.36s | 0 |
| Shop | 0.7957 | 0.890069 | 14/90 | 55.62s | 0 |
| Voucher | 0.8547 | 0.896481 | 5/90 | 69.21s | 0 |
| Web static | 0.6911 | 0.600556 | 48/90 | 159.29s | 1 |
| Web live | 0.2497 | 0.855556 | 75/90 | 47.38s | 0 |

Canonical scores include partial credit and are not end-to-end success rates.
MAI scores are published historical references, not new MAI inference. The
historical MAI optimizer/RFT scores in `summary.json` are references only.

## Files

- `baseline/*.jsonl`: unchanged raw episode receipts, including responses,
  tool traces, grades, latency, observed usage, and the retained runtime failure.
- `baseline/*.manifest.json`: portable evaluation contracts with dataset,
  catalog, runtime, grader and configuration fingerprints where recorded.
  Private endpoint fields are omitted explicitly; original manifest hashes
  are retained. These are archival contracts, not resumable job manifests.
- `summary.json`: per-task metrics, published comparisons, source pins,
  native-optimizer blocker, and links to historical references.
- `references/original-paper.json`: original ShoppingBench ASR/CAR references
  and their distinct evaluation conditions.
- `references/previous-hosted.json`: previous 250-case Product GPT-4.1 and
  GPT-6.1 Sol metrics, dataset identity, model/agent versions and source hashes.
  Those historical source trajectories are not duplicated here.
- `manifest.json`: SHA-256 inventory of the JSON/JSONL evidence, normalized to
  LF line endings. The raw episode files also match their original source hashes.

## Limitations and unfinished stages

All 450 token-usage records are incomplete; zero usage placeholders do not mean
free inference. The single failure, `web-132-rollout-2`, called unsupported tool
`web_search` and remains scored zero, with no replacement rollout.

Live-Web selection omits the published `medium` reasoning setting because Qwen
rejects it. The paper and previous hosted GPT runs use original-style ASR/CAR
and a different catalog/split; do not equate these with this canonical score.

Native Agent Optimizer rejected the existing Qwen managed-compute deployment
with HTTP 400 before job creation. No optimization or RFT training was completed;
no alternate policy model or optimization approach was substituted. RFT remains
gated by development selection, fresh calibration and verified training support.
No final-test inference ran. Published final cases were historically opened, so
a later run on them would be a published-final rerun, not a freshly sealed test.

Private cloud configuration, resource inventories and operational logs are not
part of this archive. Original published receipts elsewhere in `evals/results/`
remain unchanged.

Validate the archived evidence without Azure or new model calls:

```bash
python -m pytest tests/test_reproduction_archive.py
```
