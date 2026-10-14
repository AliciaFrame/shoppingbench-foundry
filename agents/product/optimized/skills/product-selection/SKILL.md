---
name: product-selection
description: Selects one catalog product for a constrained shopping request.
---
# Product selection

You are handling a shopping/product-selection task.

For each user query:
1. Search for candidate products using shopping tools.
2. Inspect product details for shortlisted candidates.
3. Select exactly one best-matching product.
4. Recommend that product.
5. Terminate.

## Required behavior
- Always use shopping tools to search before selecting.
- Always inspect product details with a product-information tool before recommending.
- Apply all user-specified constraints when searching and selecting, including but not limited to:
  - product type/category
  - size / fit / dimensions
  - color
  - pattern/design
  - compatibility/model
  - price range
  - shipping requirements
  - service requirements such as cash on delivery
  - store constraints such as LazMall
- If the user asks for “just products,” do not add unrelated explanation; still select and recommend one matching item.
- Use multiple reformulated searches when needed to capture all constraints.
- Prefer the best verified match from inspected candidates, not an unverified search hit.

## Important verification rule
Your final answer must be supported by the tool workflow:
- Do not recommend a product unless you have inspected it.
- Ensure the recommended product is consistent with the user’s constraints based on the product information you checked.
- The recommendation must be externally verifiable from the tool usage; avoid unsupported guesses.

## Completion protocol
- During the tool phase, do not return user-facing prose.
- Call `recommend_product` exactly once with the selected ID.
- Then call `terminate` on the next tool step.
- After termination, the runtime will request a separate concise final answer.
- In that final answer, mention only the selected product ID and explain the
  verified attributes that satisfy the request.

## Tool-use guidance
- Start with `find_product` using the user’s main constraints.
- Try additional query rewrites if needed (e.g. reorder terms, synonyms, include size/model).
- Use `service` filters when applicable, such as COD.
- Respect explicit price bounds using the tool’s price argument.
- Inspect one or more candidates with `view_product_information`.
- Only then call `recommend_product` for the single chosen item, followed by
  `terminate` on the next tool step.

## Selection rule
Recommend exactly one product, choosing the best match among inspected candidates.
If multiple products are close, prefer the one that most completely satisfies the user’s stated filters.

## Failure modes to avoid
- Do not emit user-facing text before calling `terminate`.
- Do not recommend an item without prior detail inspection.
- Do not ignore important filters like size, price, COD, LazMall, shipping, or compatibility.
- Do not provide a recommendation that cannot be justified from the inspected product details.
