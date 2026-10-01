from __future__ import annotations

import argparse
import json
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
)


def create_index(endpoint: str, index_name: str) -> None:
    credential = DefaultAzureCredential()
    client = SearchIndexClient(endpoint, credential)
    fields = [
        SimpleField(name="product_id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="shop_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="description", type=SearchFieldDataType.String),
        SearchableField(name="short_description", type=SearchFieldDataType.String),
        SimpleField(name="price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SimpleField(name="sold_count", type=SearchFieldDataType.Int64, sortable=True),
        SearchField(
            name="service",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(name="product_json", type=SearchFieldDataType.String),
    ]
    client.create_or_update_index(SearchIndex(name=index_name, fields=fields))


def upload_documents(endpoint: str, index_name: str, path: Path, batch_size: int) -> int:
    client = SearchClient(endpoint, index_name, DefaultAzureCredential())
    batch = []
    uploaded = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            product = json.loads(line)["product"]
            batch.append(
                {
                    "product_id": str(product["product_id"]),
                    "shop_id": str(product.get("shop_id", "")),
                    "title": product.get("title", ""),
                    "description": product.get("description", ""),
                    "short_description": product.get("short_description", ""),
                    "price": float(product.get("price", 0)),
                    "sold_count": int(product.get("sold_count", 0)),
                    "service": product.get("service", []),
                    "product_json": json.dumps(product, ensure_ascii=False),
                }
            )
            if len(batch) >= batch_size:
                results = client.upload_documents(batch)
                uploaded += sum(result.succeeded for result in results)
                batch.clear()
    if batch:
        results = client.upload_documents(batch)
        uploaded += sum(result.succeeded for result in results)
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--index", default="shoppingbench-products")
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    create_index(args.endpoint, args.index)
    count = upload_documents(args.endpoint, args.index, args.documents, args.batch_size)
    print(json.dumps({"uploaded": count, "index": args.index}))


if __name__ == "__main__":
    main()
