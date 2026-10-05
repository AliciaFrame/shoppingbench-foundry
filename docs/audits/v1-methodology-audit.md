# ShoppingBench v1 methodology audit

The original hill-climb evidence remains reproducible at commit `2922cdc`. The
companion `v1-evidence-manifest.json` records SHA-256 hashes for its datasets,
graders, raw receipts, aggregate results, and public claims.

The v1 results are retained as historical evidence, but they are not the final
methodology for future claims:

- The 50-case holdouts were excluded from training but reused for candidate and
  checkpoint selection, so they functioned as development sets rather than
  sealed final tests.
- Web had two target product IDs shared between RFT training and three holdout
  cases. Removing those cases still produced a substantial gain
  (`0.809574` to `0.915957`), but the original split was not product-disjoint.
- The canonical Web grader could award full credit to a wrong product containing
  the clue answer. Retained Web traces did not exploit that path.
- The generic RFT grader did not bind viewed IDs to recommended IDs. Retained
  Voucher Step 12 traces did inspect every selected product.
- Web checkpoints after Step 10 improved search efficiency while exact selection
  deteriorated, demonstrating proxy over-optimization.
- Voucher improved exact bundle selection from 46/50 to 49/50, while termination
  improved from 29/50 to 49/50. Most measured gain was protocol completion.
- User-facing output collapsed: Web Step 10 produced no nonempty explanations,
  and Voucher Step 12 produced one in 50 cases.

The v2 methodology fixes final-response handling, uses exact-first canonical and
RFT graders, binds tool evidence to selected products, creates connected
product/answer-disjoint splits, separates checkpoint development from sealed
testing, and measures repeated-rollout uncertainty.

## Offline regrade under the v2 contract

The historical traces were regraded without rerunning the models. Because the
old runtime usually ended immediately after `terminate`, no retained artifact
meets the complete v2 success contract:

| Task/artifact | V2 mean | Exact | Valid grounded answer | Bound views | Completed protocol |
|---|---:|---:|---:|---:|---:|
| Web baseline | 0.6810 | 38/50 | 7/50 | 44/50 | 40/50 |
| Web Step 10 | 0.7565 | 44/50 | 0/50 | 48/50 | 47/50 |
| Web Step 15 | 0.6940 | 40/50 | 0/50 | 46/50 | 46/50 |
| Web final | 0.6760 | 39/50 | 1/50 | 43/50 | 40/50 |
| Voucher optimized baseline | 0.8305 | 46/50 | 0/50 | 49/50 | 29/50 |
| Voucher Step 3 | 0.8528 | 47/50 | 0/50 | 49/50 | 48/50 |
| Voucher Step 12 | 0.8852 | 49/50 | 0/50 | 50/50 | 49/50 |

The exact-selection ordering still favors Web Step 10 and Voucher Step 12, but
their old traces fail the new grounded-answer requirement. Machine-readable
summaries are in `web-v1-regraded-v2.json` and
`voucher-v1-regraded-v2.json`.
