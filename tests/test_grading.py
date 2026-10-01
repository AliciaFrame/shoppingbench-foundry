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
    sample = {
        "output_tools": [
            tool("recommend_product", {"product_ids": "p1,p2"}),
            tool("terminate"),
        ]
    }
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
        },
    }
    sample = {
        "output_tools": [
            tool("recommend_product", {"product_ids": "p1,p2"}),
            tool("terminate"),
        ]
    }
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


def test_web_knowledge_attribute_can_validate_alternative_product():
    item = {
        "task": "web",
        "reward": {"product_id": "different"},
        "knowledge_attribute": "Violin",
    }
    sample = {
        "output_tools": [
            tool("recommend_product", {"product_ids": "p3"}),
            tool("terminate"),
        ]
    }
    result = grade_sample(sample, item, STORE)
    assert result["knowledge"] == 1
    assert result["score"] == 1


def test_endpoint_grader_returns_only_score():
    payload = {
        "sample": {
            "output_tools": [
                tool("recommend_product", {"product_ids": "p1"}),
                tool("terminate"),
            ]
        },
        "item": {"task": "product", "reward": {"product_id": "p1"}},
    }
    assert endpoint_grade(payload, STORE) == {"score": 1.0}
