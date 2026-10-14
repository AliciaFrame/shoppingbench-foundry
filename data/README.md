# Data

`source/` contains 900 frozen public ShoppingBench cases: 250 Product, 250
Shop, 250 Voucher, and 150 Web.

`prepared/search-documents.jsonl` contains 1,818 benchmark products plus one
deterministic distractor per product, for 3,636 searchable documents.

Regenerate the catalog and lifecycle datasets:

```powershell
python data\scripts\prepare.py
```

Target product IDs and normalized Web knowledge answers form connected groups.
Each group belongs wholly to training, development, or final test. The
training pool supplies disjoint Agent Optimizer rounds and RFT train/validation
data. Assignment and overlap checks are recorded in
`evals/datasets/manifest.json`.

The controlled catalog is designed for reproducible behavior comparisons; it
is not a claim of equivalence to the original production-scale catalog.
