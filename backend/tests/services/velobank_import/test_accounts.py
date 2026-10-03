import asyncio

import httpx

from services.velobank_import.accounts import fetch_asset_accounts


def test_account_pages_are_validated_filtered_and_sorted(monkeypatch):
    urls = []

    def handler(request):
        urls.append(request)
        page = request.url.params["page"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": page,
                        "attributes": {
                            "name": "Z" if page == "1" else "A",
                            "type": "asset",
                            "iban": None,
                        },
                    },
                    {"id": "9", "attributes": {"name": "Revenue", "type": "revenue"}},
                ],
                "links": {
                    "next": "https://untrusted.example/page" if page == "1" else None
                },
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    accounts = asyncio.run(fetch_asset_accounts("https://firefly.example/", "test"))
    assert [item.name for item in accounts] == ["A", "Z"]
    assert len(urls) == 2
    assert all(request.url.host == "firefly.example" for request in urls)
    assert urls[0].url.params["type"] == "asset"
    assert urls[0].headers["authorization"] == "Bearer test"
