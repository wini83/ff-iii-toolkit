"""Read asset accounts from the configured Firefly connection."""

import httpx
from pydantic import BaseModel, Field


class FireflyAssetAccount(BaseModel):
    id: str
    name: str
    iban: str | None = None


class _Attributes(BaseModel):
    name: str
    type: str
    iban: str | None = None


class _Account(BaseModel):
    id: str
    attributes: _Attributes


class _Page(BaseModel):
    data: list[_Account]
    links: dict = Field(default_factory=dict)


async def fetch_asset_accounts(base_url: str, token: str) -> list[FireflyAssetAccount]:
    """Follow account pages without following URLs supplied by the response."""
    accounts: list[FireflyAssetAccount] = []
    async with httpx.AsyncClient(
        timeout=30,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    ) as client:
        for page_number in range(1, 101):
            response = await client.get(
                f"{base_url.rstrip('/')}/api/v1/accounts",
                params={"type": "asset", "limit": 100, "page": page_number},
            )
            response.raise_for_status()
            page = _Page.model_validate(response.json())
            accounts.extend(
                FireflyAssetAccount(
                    id=item.id, name=item.attributes.name, iban=item.attributes.iban
                )
                for item in page.data
                if item.attributes.type == "asset"
            )
            if not page.data or not page.links.get("next"):
                return sorted(accounts, key=lambda item: item.name.casefold())
    raise ValueError("Firefly account pagination exceeded its limit.")
