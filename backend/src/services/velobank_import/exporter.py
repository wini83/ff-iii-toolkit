"""Produce a semicolon-separated CSV for Firefly III Data Importer."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from io import StringIO

from services.velobank_import.parser import VeloBankStatement, normalize

HEADERS = [
    "Date",
    "Booking date",
    "Amount",
    "Currency",
    "Payee",
    "Description",
    "Source account",
    "Destination account",
    "Source IBAN",
    "Destination IBAN",
    "External ID",
]
COUNTERPARTY = re.compile(r"Przelew (?:z|na) rachun(?:ku|ek):\s*((?:\d\s*){26})(?!\d)")
CARD = re.compile(r"^Operacja kartą\s+(.*?)na kwotę\s+.*?\s+w\s+(.+)$")
LEGACY_CARD = re.compile(
    r"^[^-]+-\s*(?P<city>[^-]+)-\s*(?P<merchant>.+?)(?:-\s*|\s+)\d{3}$"
)


def _card_details(description: str) -> tuple[str, str] | None:
    """Extract the merchant and location without changing the bank's raw text."""
    card = CARD.fullmatch(normalize(description))
    if not card:
        return None
    details = card[2].strip()
    # The older format omits the card number and includes the holder and a
    # trailing numeric code. Limit this interpretation to that variant.
    legacy = LEGACY_CARD.fullmatch(details) if not card[1].strip() else None
    if legacy:
        merchant = legacy["merchant"].strip()
        return merchant, f"{merchant}, {legacy['city'].strip()}"
    merchant = details.split(",", 1)[0].strip()
    if not merchant:
        return None
    return merchant, details


def export_rows(
    statement: VeloBankStatement, *, account_name: str, own_accounts: dict[str, str]
) -> list[dict[str, str]]:
    """Export signed amounts and preserve identical legitimate operations.

    Args:
        statement: Parsed VeloBank account history.
        account_name: Existing Firefly asset account representing this account.
        own_accounts: Other own Polish IBANs mapped to their Firefly account names.

    Returns:
        CSV with dates, descriptions, account mapping and generated external IDs.
    """
    rows: list[dict[str, str]] = []
    occurrences: Counter[str] = Counter()
    for tx in statement.transactions:
        card = _card_details(tx.description)
        name = re.search(
            r"(?:Nadawca|Odbiorca): (.*?)(?:, Tytuł:| Tytuł:|$)", tx.description
        )
        payee = (
            card[0] if card else name[1].rstrip(",") if name else "Unknown counterparty"
        )
        account_match = COUNTERPARTY.search(tx.description)
        other_iban = "PL" + re.sub(r"\s", "", account_match[1]) if account_match else ""
        other_name = own_accounts.get(other_iban, payee)
        this_iban = "PL" + statement.account
        outgoing = tx.amount < 0
        # No statement date, filename, page number or global row index in the ID.
        key = json.dumps(
            [
                statement.account,
                tx.date.isoformat(),
                tx.booking_date.isoformat(),
                f"{tx.amount:.2f}",
                tx.currency,
                normalize(tx.description),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(key.encode()).hexdigest()
        occurrences[digest] += 1
        rows.append(
            {
                "Date": tx.date.isoformat(),
                "Booking date": tx.booking_date.isoformat(),
                "Amount": f"{tx.amount:.2f}",
                "Currency": tx.currency,
                "Payee": payee,
                "Description": card[1] if card else tx.description,
                "Source account": account_name if outgoing else other_name,
                "Destination account": other_name if outgoing else account_name,
                "Source IBAN": this_iban if outgoing else other_iban,
                "Destination IBAN": other_iban if outgoing else this_iban,
                "External ID": f"velobank-v1-{digest}-{occurrences[digest]}",
            }
        )
    return rows


def render_csv(
    statement: VeloBankStatement, *, account_name: str, own_accounts: dict[str, str]
) -> str:
    """Render the same account mapping and identifiers used by the web preview."""
    return render_rows(
        export_rows(statement, account_name=account_name, own_accounts=own_accounts)
    )


def render_rows(rows: list[dict[str, str]]) -> str:
    """Serialize already mapped rows, including a chunk of a complete statement."""
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
