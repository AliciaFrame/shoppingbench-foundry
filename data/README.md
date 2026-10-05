# Data

`source/` contains the 900 frozen public ShoppingBench cases used by this demo:

- Product: 250
- Shop: 250
- Voucher: 250
- Web: 150

`prepared/search-documents.jsonl` preserves the v1 deterministic
3,636-document catalog. `prepared/search-documents-v2.jsonl` is the remediated
catalog, including all valid SKU variants needed to independently revalidate
ground-truth metadata.

Both catalogs contain:
1,818 gold products and one controlled distractor for each gold product.
`prepared/evaluation-dataset.jsonl` is the combined grader-ready export.

Regenerate prepared data and evaluation splits:

```powershell
python data\scripts\prepare.py
```

The default v2 split generator creates connected groups using target product
IDs and, for Web, normalized knowledge answers. A connected group is assigned
wholly to one of:

- training pool
- 30-case checkpoint-development set
- 50-case sealed final-test set

The training pool also supplies deterministic 20-case and 40-case Agent
Optimizer subsets. RFT preparation divides the same training pool into
group-disjoint training and validation sets. The full assignment, leakage keys,
and zero-overlap assertions are stored in
`evaluations/datasets/v2/split-manifest.json`.

Run `python data\scripts\prepare.py --version v1` only to reproduce the
historical row-level optimizer and holdout splits.

The controlled catalog is designed for reproducible optimization experiments,
not as a claim of equivalence to the original multi-million-product environment.
