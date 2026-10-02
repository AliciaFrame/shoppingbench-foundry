param(
    [string]$Output = "rft\results\web-job-rft3-rerun.json",
    [switch]$ConfirmSubmit
)

if (-not $ConfirmSubmit) {
    throw "Pass -ConfirmSubmit to create a billable Web RFT job."
}

python -m rft.scripts.submit web `
    --calibration rft/results/web-step0-calibration-v2.json `
    --grader-version v2 `
    --n-epochs 2 `
    --batch-size 8 `
    --learning-rate-multiplier 1.0 `
    --eval-interval 5 `
    --eval-samples 1 `
    --max-episode-steps 12 `
    --confirm-submit `
    --output $Output
