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


def render_csv(
    statement: VeloBankStatement, *, account_name: str, own_accounts: dict[str, str]
) -> str:
    """Export signed amounts and preserve identical legitimate operations.

    Args:
        statement: Parsed VeloBank account history.
        account_name: Existing Firefly asset account representing this account.
        own_accounts: Other own Polish IBANs mapped to their Firefly account names.

    Returns:
        CSV with dates, descriptions, account mapping and generated external IDs.
    """
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS, delimiter=";")
    writer.writeheader()
    occurrences: Counter[str] = Counter()
    for tx in statement.transactions:
        card = re.search(r"Operacja kartą .*? na kwotę .*? w (.+)", tx.description)
        name = re.search(
            r"(?:Nadawca|Odbiorca): (.*?)(?:, Tytuł:| Tytuł:|$)", tx.description
        )
        payee = (
            card[1] if card else name[1].rstrip(",") if name else "Unknown counterparty"
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
        writer.writerow(
            {
                "Date": tx.date.isoformat(),
                "Booking date": tx.booking_date.isoformat(),
                "Amount": f"{tx.amount:.2f}",
                "Currency": tx.currency,
                "Payee": payee,
                "Description": tx.description,
                "Source account": account_name if outgoing else other_name,
                "Destination account": other_name if outgoing else account_name,
                "Source IBAN": this_iban if outgoing else other_iban,
                "Destination IBAN": other_iban if outgoing else this_iban,
                "External ID": f"velobank-v1-{digest}-{occurrences[digest]}",
            }
        )
    return output.getvalue()
