param(
    [string]$Output = "rft\results\v2\web-v3-submission.json",
    [switch]$ConfirmSubmit
)

if (-not $ConfirmSubmit) {
    throw "Pass -ConfirmSubmit to create a billable Web RFT job."
}

python -m rft.scripts.submit web `
    --calibration rft/results/v2/web-v3-calibration.json `
    --data-dir rft/data/v2 `
    --grader-version v3 `
    --n-epochs 2 `
    --batch-size 8 `
    --learning-rate-multiplier 0.5 `
    --eval-interval 3 `
    --eval-samples 5 `
    --max-episode-steps 12 `
    --confirm-submit `
    --output $Output

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
