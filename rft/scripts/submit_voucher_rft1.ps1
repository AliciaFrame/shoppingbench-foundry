param(
    [string]$Output = "rft\results\voucher-job-rft1-rerun.json",
    [switch]$ConfirmSubmit
)

if (-not $ConfirmSubmit) {
    throw "Pass -ConfirmSubmit to create a billable Voucher RFT job."
}

python -m rft.scripts.submit voucher `
    --calibration rft/results/voucher-calibration.json `
    --n-epochs 1 `
    --batch-size 8 `
    --learning-rate-multiplier 0.5 `
    --eval-interval 3 `
    --eval-samples 3 `
    --max-episode-steps 12 `
    --confirm-submit `
    --output $Output
