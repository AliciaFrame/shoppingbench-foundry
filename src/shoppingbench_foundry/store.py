from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

SEARCH_FIELDS = ["product_id", "shop_id", "title", "price", "service", "sold_count"]
INFORMATION_FIELDS = [
    "product_id",
    "short_description",
    "description",
    "sku_options",
    "attributes",
]
VALID_SERVICES = {"official", "freeShipping", "COD", "flashsale"}


class ProductStore(Protocol):
    def get_products(self, product_ids: Sequence[str]) -> list[dict[str, Any]]: ...

    def search(
        self,
        query: str,
        page: int,
        shop_id: str | None = None,
        price: str | None = None,
        sort: str | None = None,
        service: str | None = None,
    ) -> list[dict[str, Any]]: ...


def _parse_price(price: str | None) -> tuple[float | None, float | None]:
    if not price or "-" not in price:
        return None, None
    parts = price.split("-")
    if len(parts) != 2:
        return None, None

    def convert(value: str) -> float | None:
        try:
            return float(value) if value else None
        except ValueError:
            return None

    return convert(parts[0]), convert(parts[1])


def _parse_services(service: str | None) -> list[str]:
    if not service:
        return []
    return list(dict.fromkeys(item for item in service.split(",") if item in VALID_SERVICES))


def _matches_filters(
    product: dict[str, Any],
    shop_id: str | None,
    price: str | None,
    service: str | None,
) -> bool:
    low, high = _parse_price(price)
    product_price = float(product.get("price", 0))
    if shop_id and str(product.get("shop_id")) != shop_id:
        return False
    if low is not None and product_price < low:
        return False
    if high is not None and product_price > high:
        return False
    product_services = set(product.get("service", []))
    return all(item in product_services for item in _parse_services(service))


class InMemoryProductStore:
    def __init__(self, products: Iterable[dict[str, Any]]):
        self._products = {str(product["product_id"]): product for product in products}

    def get_products(self, product_ids: Sequence[str]) -> list[dict[str, Any]]:
        return [
            self._products[product_id] for product_id in product_ids if product_id in self._products
        ]

    def search(
        self,
        query: str,
        page: int,
        shop_id: str | None = None,
        price: str | None = None,
        sort: str | None = None,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        if page < 1 or page > 5:
            return []
        terms = {term.lower() for term in query.split() if term}
        matches = [
            product
            for product in self._products.values()
            if terms.intersection(str(product.get("title", "")).lower().split())
            and _matches_filters(product, shop_id, price, service)
        ]
        if sort == "order":
            matches.sort(key=lambda item: item.get("sold_count", 0), reverse=True)
        elif sort == "priceasc":
            matches.sort(key=lambda item: item.get("price", 0))
        elif sort == "pricedesc":
            matches.sort(key=lambda item: item.get("price", 0), reverse=True)
        start = (page - 1) * 10
        return [
            {field: product.get(field) for field in SEARCH_FIELDS}
            for product in matches[start : start + 10]
        ]


class AzureAISearchProductStore:
    def __init__(self, endpoint: str, index_name: str, api_key: str | None = None):
        credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
        self._client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)

    @classmethod
    def from_environment(cls) -> AzureAISearchProductStore:
        endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        index_name = os.getenv("AZURE_SEARCH_INDEX", "shoppingbench-products")
        return cls(endpoint, index_name, os.getenv("AZURE_SEARCH_API_KEY"))

    @staticmethod
    def _decode_product(document: dict[str, Any]) -> dict[str, Any]:
        raw = document.get("product_json")
        return json.loads(raw) if raw else dict(document)

    def get_products(self, product_ids: Sequence[str]) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        for product_id in product_ids:
            try:
                document = self._client.get_document(key=product_id)
            except ResourceNotFoundError:
                continue
            products.append(self._decode_product(document))
        return products

    def search(
        self,
        query: str,
        page: int,
        shop_id: str | None = None,
        price: str | None = None,
        sort: str | None = None,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        if page < 1 or page > 5:
            return []
        filters: list[str] = []
        if shop_id:
            escaped_shop_id = shop_id.replace("'", "''")
            filters.append(f"shop_id eq '{escaped_shop_id}'")
        low, high = _parse_price(price)
        if low is not None:
            filters.append(f"price ge {low}")
        if high is not None:
            filters.append(f"price le {high}")
        for item in _parse_services(service):
            filters.append(f"service/any(s: s eq '{item}')")
        order_by = {
            "order": ["sold_count desc"],
            "priceasc": ["price asc"],
            "pricedesc": ["price desc"],
        }.get(sort)
        results = self._client.search(
            search_text=query,
            filter=" and ".join(filters) or None,
            order_by=order_by,
            skip=(page - 1) * 10,
            top=10,
            select=SEARCH_FIELDS,
        )
        return [{field: document.get(field) for field in SEARCH_FIELDS} for document in results]


def create_store() -> ProductStore:
    backend = os.getenv("SHOPPINGBENCH_STORE", "azure-search")
    if backend != "azure-search":
        raise ValueError(f"Unsupported SHOPPINGBENCH_STORE: {backend}")
    return AzureAISearchProductStore.from_environment()
