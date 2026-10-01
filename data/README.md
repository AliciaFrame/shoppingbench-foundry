# Data

`source/` contains the 900 frozen public ShoppingBench cases used by this demo:

- Product: 250
- Shop: 250
- Voucher: 250
- Web: 150

`prepared/search-documents.jsonl` is a deterministic 3,636-document catalog:
1,818 gold products and one controlled distractor for each gold product.
`prepared/evaluation-dataset.jsonl` is the combined grader-ready export.

Regenerate prepared data and evaluation splits:

```powershell
python data\scripts\prepare.py
```

The split generator is seeded per task. It creates:

- 20 round-one optimization cases
- 40 disjoint round-two optimization cases
- 50 untouched canonical holdout cases

The controlled catalog is designed for reproducible optimization experiments,
not as a claim of equivalence to the original multi-million-product environment.
