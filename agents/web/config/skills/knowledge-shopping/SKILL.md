---
name: knowledge-shopping
description: Resolves a factual clue before searching for a matching product.
---

# Knowledge-grounded shopping

Infer the entity or attribute implied by the question, use it in catalog
search, verify it in product details, recommend one ID, and terminate.

During the tool phase, do not return user-facing prose. Call
`recommend_product`, then call `terminate` on the next tool step. The runtime
then requests a separate concise final answer that explicitly states:

- the resolved factual clue,
- the recommended product ID,
- how the clue connects to the selected product.

Do not present a product as grounded unless its details were viewed before
recommendation. In the final answer, mention only the recommended product ID.
