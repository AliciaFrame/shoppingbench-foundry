---
name: voucher-budget
description: Applies platform or shop voucher arithmetic to a product set.
---
You are solving a shopping task with voucher-budget constraints.

Goal:
- Use shopping tools to find products that match every user-requested attribute.
- Recommend product IDs in the correct order.
- Crucially, before recommending, explicitly verify voucher feasibility using:
  1. minimum spend threshold,
  2. discount type,
  3. discount cap,
  4. final payable amount versus the user’s budget,
  5. same-shop requirement.

Input format:
- The user asks for one or more products, each with attribute constraints such as category, color, size, quantity/pack type, service tags (e.g. COD, LazMall, LazFlash, free delivery), capacity, material, etc.
- The user also gives:
  - a total budget,
  - voucher rules:
    - same-shop only or equivalent wording,
    - minimum subtotal threshold,
    - discount type and value (currently percentage discount in examples),
    - discount cap.

Required workflow:
1. Parse all requested product slots and all required attributes for each slot.
2. Search with shopping tools for each slot using targeted queries.
3. Inspect candidate product details with product-information tools before deciding.
4. Build a bundle that satisfies the user’s requested attributes.
5. Check voucher eligibility across the entire bundle:
   - All recommended items must come from the same shop if the voucher says so.
   - Compute subtotal before discount.
   - Confirm subtotal exceeds/meets the voucher threshold exactly as stated.
   - Compute discount according to the voucher type.
     - For percentage discount: discount = percentage × subtotal.
     - Apply the cap: actual discount = min(computed discount, cap).
   - Compute final payable = subtotal - actual discount.
   - Confirm final payable is within the user’s budget.
6. Only then recommend the ordered product IDs.
7. Terminate the episode after recommendation or after concluding no valid bundle exists.

Output requirements:
- Return a usable result, not just a bare JSON stub.
- Include the final recommended_product_ids in the exact order corresponding to the user’s requested product slots.
- Also provide a concise voucher-check explanation showing:
  - shop consistency,
  - subtotal,
  - threshold check,
  - raw discount,
  - capped discount,
  - final payable,
  - whether it fits the budget.
- If no valid bundle exists, say so clearly and briefly explain which constraint failed (e.g. no same-shop match, threshold not met, budget exceeded after capped discount, missing requested attribute, etc.).
- If the environment expects structured output, still include the recommendation and voucher reasoning in assistant_text or equivalent human-readable field, not only tool traces.

Tool-use expectations:
- You must use shopping tools (e.g. search + product detail inspection) before recommending.
- Do not recommend unverified product IDs.
- Avoid repetitive or aimless searches; refine queries based on missing attributes.
- Inspect enough product details to confirm requested constraints and shop IDs.

Important task-specific lessons from prior failures:
- Do not output only product IDs and tool calls.
- Do not omit prices, shop matching, or voucher math.
- Do not recommend items unless you have evidence they satisfy the requested attributes and voucher constraints.
- Do not terminate with an empty or unsupported answer when a justified partial conclusion is possible.
- The main evaluation is sensitive to voucher-budget reasoning, so always show the threshold, discount type, cap, final budget calculation, and same-shop check before recommending.

Recommended response structure:
1. Short summary of matched items.
2. Voucher check:
   - Same shop: yes/no
   - Subtotal: X
   - Threshold needed: Y
   - Discount: Z% of subtotal = A
   - Cap: B
   - Actual discount: min(A, B) = C
   - Final payable: subtotal - C = D
   - Budget: E
   - Result: within budget / not within budget
3. recommended_product_ids: [ ... ] in exact requested order
4. Terminate

If no valid bundle:
1. State no valid recommendation found.
2. Briefly state the blocking constraint(s).
3. recommended_product_ids: []
4. Terminate
