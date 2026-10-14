# Pricing and cost calculations

Use **measured usage multiplied by source-backed rates**, not token price
alone and not one model's token count applied to another model.

## Retrieve a price snapshot

1. Start with the user's primary public reference:
   [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/).
   It may redirect to the current Azure pricing page. Match exact model/version,
   region, deployment SKU, billing mode and context tier; distinguish inference,
   training and checkpoint hosting prices.
2. If the model/rate is absent or the page renders placeholders such as `$-`,
   use [GitHub models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)
   as the requested fallback. Its per-token rates are useful reference
   estimates; they are not automatically an Azure invoice quote. Label the
   basis `copilot_reference` for an Azure workload.
3. Verify the billing unit. A dollars-per-million-token table can feed this
   calculator. Premium-request multipliers, credits, hours and monthly
   allowances cannot be substituted as token rates. If using an AI-credit
   conversion, record the source's current conversion explicitly.
4. Save model/SKU/region/tier, currency, rates, source URL, retrieval date and
   estimate basis under the run directory. Preserve the source excerpt or
   quote separately. Never invent missing rates or translate `$-` to zero.
   If neither source has an applicable value, request an approved quote or
   report an unpriced component.

The pages change. Fetch them at execution time rather than treating a sample
rate in this skill or a previous experiment as permanently current. Azure
pricing can be dynamically rendered; an unavailable numeric rate is not a
free model. Negotiated discounts and provider billing may differ from list
and reference estimates.

## Normalize usage without double counting

Capture every successful returned model response, including partial failed
episodes and serving-time dependencies. Keep raw SDK usage and normalize
charge quantities:

| Bucket | Meaning |
|---|---|
| `uncached_input_tokens` | Input charged at the ordinary input rate |
| `cached_input_tokens` | Cached reads charged at the cached-input rate |
| `cache_write_tokens` | Writes charged at the provider's cache-write rate |
| `output_tokens` | All billed output, including reasoning when the provider includes it |

An SDK's `input_tokens` often already includes cached input: subtract the
cached portion before assigning ordinary input. Check write semantics:
when writes replace ordinary input charges, exclude writes from uncached
input; if they are an additional surcharge, keep the ordinary charge and
also record the write bucket. Do not impose one provider's interpretation
on another.

Reasoning tokens that are already part of `output_tokens` must not be added
again. Native search/tool fees, training and hosting are separate charges.
Choose context tiers from the **per-call physical context input**, not the
episode sum or only the uncached portion. A long-context tier can change the
rate of the whole call, not merely tokens above a boundary.

If cached breakdown is unavailable, an explicitly labeled all-input-uncached
estimate is possible using reported total input; do not pretend that it is
measured cache accounting. Missing output or request usage remains unknown.

## Calculator inputs and formula

`scripts/cost_report.py` consumes the normalized JSONL episodes described in
[the contract](contract.md) and a pricebook:

```json
{
  "schema_version": 1,
  "currency": "USD",
  "prices": {
    "policy-default": {
      "model": "<exact-model-version>",
      "provider": "<provider>",
      "region": "<region>",
      "sku": "<deployment-sku>",
      "tier": "<context-tier>",
      "source_url": "<retrieved-price-url>",
      "retrieved_at": "<YYYY-MM-DD>",
      "basis": "azure_list",
      "unit": "per_million_tokens",
      "cache_write_mode": "<none-replacement-or-additional>",
      "min_context_tokens": 0,
      "max_context_tokens": null,
      "rates": {
        "uncached_input_tokens": null,
        "cached_input_tokens": null,
        "cache_write_tokens": null,
        "output_tokens": null
      }
    }
  }
}
```

Resolve placeholders before paid work. Use a separate key for each model,
SKU or context tier, and record the chosen `pricing_key` on every call.
The helper validates bounded tiers against `context_input_tokens`. A zero
bucket does not need a rate; a positive or unknown bucket does.
`cache_write_mode` must be `none`, `replacement`, or `additional` according
to the source; positive write usage requires a verified non-`none` mode.

For each known bucket:

`cost = charged_tokens * rate_per_million / 1,000,000`

Sum across every call. Keep `inference` (answer-serving), `evaluation`
(judges), and `optimizer` purposes separate. Serving cost per 1,000 episodes
uses all scheduled episodes, including failures; successful-only cost is a
different metric and must not silently replace it.

The optional `--charges` JSON file holds already quantified non-token costs:

```json
[{"stage":"baseline","purpose":"hosting","label":"approved hosting period",
  "amount":null,"currency":"USD","source":"<quote-or-billing-source>"}]
```

Training, hosting, optimizer and evaluation charges contribute to experiment
spend, not answer-serving cost by default. An explicitly allocated `serving`
charge must use `allocation: "this_run"`; only then is it amortized across
that stage's episodes. Keep provider units, quantities and allocation
evidence in the source receipt.

## Present results honestly

The report includes known subtotals, nullable complete totals, incomplete
usage/rate counts, token charge buckets by purpose, failures, stage metrics and pricing bases. It is a
recorded-usage estimate, not an invoice. Unknown charges must remain unknown.
Charge buckets are not a physical total when cache writes are surcharges;
use the preserved raw provider totals for a physical-token comparison.

Show baseline -> optimized -> RFT quality, latency and serving cost together,
plus experiment cost and any approved training amortization/break-even
analysis. A shorter output does not guarantee fewer total tokens: repeated
input context and expensive reasoning matter. Never claim an unpriced
candidate is the cheapest.
