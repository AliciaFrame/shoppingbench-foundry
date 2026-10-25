# Qwen two-instruction-arm RFT investigation

**Status: calibration in progress. No RFT job, checkpoint or RFT improvement
has been established.** `summary.json` describes the dated snapshot, not a live
progress feed. Partial stages deliberately have no aggregate performance means.

The two conditions use original instructions or Alicia's published optimized
instructions (including their skills), with identical tools, Qwen policy,
validation cases, reward and execution bounds. The current campaign schedules
100 Voucher validation episodes and 105 static-Web validation episodes **per
instruction arm**. These are fresh model/tool runs, not historical score replay.
Calibration is not development or final-test evaluation.

The calibration harness uses ordinary native Chat inference and authenticated
hosted catalog-tool adapters. All declared tools remain visible, without forced
recommendation or final-answer repair. This original-instruction condition is
therefore not runtime-equivalent to the earlier off-the-shelf Responses baseline.
It is not a trainable RLE capture or a verified service-native training rollout.

Training inputs are isolated by connected groups. Actual committed and
regenerated static-Web inputs contain **49 training and 21 validation rows**;
the documentation's nominal 50/20 counts are not the observed artifact counts.
Voucher has 150/20 rows. Reviewed live-Web training inputs have 38/21 rows and
reuse the published frozen evidence explicitly as training input, not fresh
evaluation evidence. Both arms differ only in their developer instructions.

`preflight/` records paired-input fingerprints and native grader checks.
Acceptance of all three grader schemas and successful deterministic grader
checks do not prove Qwen-specific live-tool RFT compatibility. Schema-validation
thresholds are API preflight values, not approved training-job settings.
Calibration must assess the published reward boundaries separately.
Mapping the native
training alias to the existing serving asset and sufficient calibration signal
remain gates. A project-scoped managed-identity permission repair restored the
live-Web grader: grounded, unsupported-claim and empty fixtures matched local
scores of 1.0, 0.493762 and 0.0. GPT-5.4 is the fixed judge, not the
shopping policy. No new final-test inference has been performed; the published
final split has historical exposure.

`calibration/` preserves every completed episode available at the snapshot,
including failures. `manifest.json` binds the copied artifacts by SHA-256.
Private endpoint configuration, authorization and live process state remain in
the ignored run directory `.foundry/hillclimb/mtas-rft-20261025/`.
