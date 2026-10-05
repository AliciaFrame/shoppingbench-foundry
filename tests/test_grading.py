from shoppingbench_foundry.grading import endpoint_grade, grade_sample, score_product
from shoppingbench_foundry.store import InMemoryProductStore

PRODUCTS = [
    {
        "product_id": "p1",
        "shop_id": "s1",
        "title": "Plain High Waist Baggy Denim Jeans",
        "price": 500.0,
        "sold_count": 10,
        "service": ["flashsale"],
        "sku_options": {"1": {"size": "eu:30", "color_family": "blue"}},
        "attributes": {
            "jeans_fit_type": ["baggy fit"],
            "waist_type": ["high"],
            "clothing_material": ["denim"],
            "fa_pattern": ["plain"],
        },
        "description": "",
        "short_description": "",
    },
    {
        "product_id": "p2",
        "shop_id": "s1",
        "title": "Basic Calculator",
        "price": 200.0,
        "sold_count": 5,
        "service": ["COD"],
        "sku_options": {"1": {"color": "orange 1pcs"}},
        "attributes": {"calculator_power_source": ["battery"], "calculator_type": ["basic"]},
        "description": "",
        "short_description": "",
    },
    {
        "product_id": "p3",
        "shop_id": "s2",
        "title": "Violin Horsetail Bow",
        "price": 300.0,
        "sold_count": 2,
        "service": [],
        "sku_options": {},
        "attributes": {},
        "description": "Traditional horsetail bow for violin players.",
        "short_description": "",
    },
]
STORE = InMemoryProductStore(PRODUCTS)


def tool(name, arguments=None):
    return {"function": {"name": name, "arguments": arguments or {}}}


def complete_sample(product_ids, query, assistant_text):
    return {
        "output_tools": [
            tool("find_product", {"q": query, "page": 1}),
            tool("view_product_information", {"product_ids": product_ids}),
            tool("recommend_product", {"product_ids": product_ids}),
            tool("terminate"),
        ],
        "assistant_text": assistant_text,
    }


def test_product_score_matches_all_constraints():
    reward = {
        "product_id": "different",
        "title": ["Plain High Waist Baggy Denim Jeans"],
        "sku_options": [{"size": "eu:30"}],
        "attributes": [
            {"jeans_fit_type": ["baggy fit"]},
            {"waist_type": ["high"]},
            {"clothing_material": ["denim"]},
            {"fa_pattern": ["plain"]},
        ],
        "price": [{"greater than": [114, None]}],
    }
    assert score_product(PRODUCTS[0], reward).rule == 1


def test_shop_requires_all_products_from_same_shop():
    item = {
        "task": "shop",
        "reward": [{"product_id": "p1"}, {"product_id": "p2"}],
    }
    sample = complete_sample("p1,p2", "denim calculator", "I recommend p1 and p2.")
    result = grade_sample(sample, item, STORE)
    assert result["success"] is True
    assert result["task_invariant"] == 1


def test_voucher_applies_fixed_discount():
    item = {
        "task": "voucher",
        "reward": [{"product_id": "p1"}, {"product_id": "p2"}],
        "voucher": {
            "voucher_type": "shop",
            "threshold": 600,
            "discount_type": "fixed",
            "face_value": 100,
            "discount": None,
            "cap": None,
            "budget": 600,
            "price_after_voucher": 600,
        },
    }
    sample = complete_sample(
        "p1,p2",
        "denim calculator",
        (
            "p1 and p2 are from one shop. The subtotal is 700, the threshold is 600, "
            "and the voucher discount leaves 600 within budget."
        ),
    )
    result = grade_sample(sample, item, STORE)
    assert result["success"] is True
    assert result["task_invariant"] == 1


def test_shop_voucher_rejects_products_from_different_shops():
    item = {
        "task": "voucher",
        "reward": [{"product_id": "p1"}, {"product_id": "p3"}],
        "voucher": {
            "voucher_type": "shop",
            "threshold": 700,
            "discount_type": "percentage",
            "face_value": None,
            "discount": 0.5,
            "cap": 500,
            "budget": 500,
        },
    }
    sample = {
        "output_tools": [
            tool("recommend_product", {"product_ids": "p1,p3"}),
            tool("terminate"),
        ]
    }
    result = grade_sample(sample, item, STORE)
    assert result["success"] is False
    assert result["task_invariant"] == 0


def test_web_knowledge_attribute_cannot_validate_wrong_product():
    item = {
        "task": "web",
        "reward": {"product_id": "different"},
        "knowledge_attribute": "Violin",
    }
    sample = complete_sample(
        "p3",
        "violin bow",
        "The clue resolves to Violin, so I recommend p3.",
    )
    result = grade_sample(sample, item, STORE)
    assert result["knowledge"] == 1
    assert result["ground_truth"] == 0
    assert result["success"] is False
    assert result["score"] <= 0.3


def test_correct_id_does_not_hide_stale_metadata():
    reward = {
        "product_id": "p1",
        "title": ["Wrong title"],
        "attributes": [{"brand": ["wrong"]}],
    }

    result = score_product(PRODUCTS[0], reward)

    assert result.ground_truth == 1
    assert result.rule == 0


def test_endpoint_grader_returns_only_score():
    payload = {
        "sample": complete_sample("p1", "denim jeans", "I recommend p1."),
        "item": {
            "task": "product",
            "reward": {
                "product_id": "p1",
                "title": ["Plain High Waist Baggy Denim Jeans"],
            },
        },
    }
    assert endpoint_grade(payload, STORE) == {"score": 1.0}


def test_final_answer_rejects_an_extra_product_id():
    products = [
        {
            "product_id": "1234567890",
            "shop_id": "s1",
            "title": "Target",
            "price": 10,
            "service": [],
            "sku_options": {},
            "attributes": {},
            "description": "",
            "short_description": "",
        }
    ]
    item = {
        "task": "product",
        "query": "Find the matching product.",
        "reward": {"product_id": "1234567890", "title": ["Target"]},
    }
    sample = complete_sample(
        "1234567890",
        "matching product",
        "I recommend 1234567890. You could also consider 9999999999.",
    )

    result = grade_sample(sample, item, InMemoryProductStore(products))

    assert result["valid_final_answer"] is False
    assert result["success"] is False


def test_final_answer_does_not_treat_decimal_digits_as_product_id():
    products = [
        {
            "product_id": "1234567890",
            "shop_id": "s1",
            "title": "Target",
            "price": 7650,
            "service": [],
            "sku_options": {},
            "attributes": {},
            "description": "",
            "short_description": "",
        }
    ]
    item = {
        "task": "voucher",
        "query": "Find the matching product.",
        "reward": {"product_id": "1234567890", "title": ["Target"]},
        "voucher": {
            "voucher_type": "shop",
            "threshold": 6649,
            "discount_type": "percentage",
            "face_value": None,
            "discount": 0.41,
            "cap": 4362,
            "budget": 4638,
            "price_after_voucher": 4513.500000000001,
        },
    }
    sample = complete_sample(
        "1234567890",
        "matching product",
        (
            "I recommend 1234567890 from the same shop. The subtotal is 7650.0, "
            "which exceeds the 6649 threshold. The voucher discount gives a final "
            "payable of 4513.500000000001 within budget."
        ),
    )

    result = grade_sample(sample, item, InMemoryProductStore(products))

    assert result["valid_final_answer"] is True
    assert result["success"] is True
