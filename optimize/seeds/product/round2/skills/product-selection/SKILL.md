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

## Output requirements
Return a structured result that:
- identifies the task as a product task
- includes the recommended product ID in `recommended_product_ids`
- sets `terminated` to true
- includes the tool calls used in `output_tools`
- includes a non-empty `assistant_text`

## assistant_text requirements
`assistant_text` must not be empty. It should briefly state:
- the selected product
- why it matches the key constraints
- any especially relevant verified attributes (for example size, color, price range, COD, LazMall, free/complimentary shipping, compatibility)

Keep it concise, but include enough detail to make the recommendation usable and verifiable.

## Tool-use guidance
- Start with `find_product` using the user’s main constraints.
- Try additional query rewrites if needed (e.g. reorder terms, synonyms, include size/model).
- Use `service` filters when applicable, such as COD.
- Respect explicit price bounds using the tool’s price argument.
- Inspect one or more candidates with `view_product_information`.
- Only then call `recommend_product` for the single chosen item.
- Finally call `terminate`.

## Selection rule
Recommend exactly one product, choosing the best match among inspected candidates.
If multiple products are close, prefer the one that most completely satisfies the user’s stated filters.

## Failure modes to avoid
- Do not leave `assistant_text` empty.
- Do not recommend an item without prior detail inspection.
- Do not ignore important filters like size, price, COD, LazMall, shipping, or compatibility.
- Do not provide a recommendation that cannot be justified from the inspected product details.
