# Published RFT job receipts

`voucher/`, `web-v5/`, and `web-search-v6/` contain the calibration,
submission, terminal status, deployment, and selection evidence captured for
the published experiments. `web-search-v6/` also contains the frozen
base-model rollouts its calibration scored and the training curve.

These files are evidence, not reusable configuration:

- job, file, checkpoint, and fine-tuned model IDs belong to the original Azure
  resource;
- availability of the recorded model and training type is subscription- and
  region-dependent;
- a new submission creates new IDs and may support different SKUs;
- checkpoint quality decisions are recorded separately under
  `evals/results/checkpoints/` and `evals/results/comparisons/`.

No credentials or API keys are stored in these receipts. Resource, job, file,
model, and checkpoint identifiers are historical evidence only.
