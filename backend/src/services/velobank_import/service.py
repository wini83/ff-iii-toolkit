"""Shared preview and download projection, using the CLI's export engine."""

from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from services.velobank_import.exporter import export_rows, render_rows
from services.velobank_import.session import ImportPreview


def preview_data(entry: ImportPreview, accounts: dict[str, str]) -> dict:
    statement = entry.statement
    iban = "PL" + statement.account
    name = accounts.get(iban, "")
    rows = export_rows(statement, account_name=name, own_accounts=accounts)
    dates = [tx.date for tx in statement.transactions]
    totals = []
    for currency in sorted({tx.currency for tx in statement.transactions}):
        amounts = [
            tx.amount for tx in statement.transactions if tx.currency == currency
        ]
        totals.append(
            {
                "currency": currency,
                "debits": f"{sum((-a for a in amounts if a < 0), Decimal('0')):.2f}",
                "credits": f"{sum((a for a in amounts if a > 0), Decimal('0')):.2f}",
            }
        )
    return {
        "file_id": entry.id,
        "expires_at": entry.expires_at,
        "account_iban": iban,
        "account_name": name,
        "start_date": min(dates),
        "end_date": max(dates),
        "record_count": len(rows),
        "totals": totals,
        "preview": [
            {
                "id": row["External ID"],
                "date": row["Date"],
                "booking_date": row["Booking date"],
                "amount": row["Amount"],
                "currency": row["Currency"],
                "payee": row["Payee"],
                "description": row["Description"],
                "source_account": row["Source account"],
                "destination_account": row["Destination account"],
                "source_iban": row["Source IBAN"],
                "destination_iban": row["Destination IBAN"],
                "needs_review": row["Payee"] == "Unknown counterparty"
                and row[
                    "Source IBAN" if Decimal(row["Amount"]) > 0 else "Destination IBAN"
                ]
                not in accounts,
            }
            for row in rows
        ],
        "warnings": [
            "Generated identifiers do not match GoCardless IDs. Review overlapping histories and transfers imported from other banks.",
            "Export complete days and all operation types: partial sets of identical operations can make identifiers ambiguous.",
        ]
        + (
            []
            if name
            else ["Choose the Firefly account for this statement before exporting."]
        ),
    }


def export_payload(
    entry: ImportPreview, accounts: dict[str, str], chunk_size: int | None
) -> tuple[bytes, str, str]:
    name = accounts.get("PL" + entry.statement.account)
    if not name:
        raise ValueError(
            "Choose the Firefly account for this statement before exporting."
        )
    rows = export_rows(entry.statement, account_name=name, own_accounts=accounts)
    dates = [tx.date for tx in entry.statement.transactions]
    stem = f"velobank_{min(dates):%Y%m%d}_{max(dates):%Y%m%d}"
    if chunk_size is None:
        return (
            render_rows(rows).encode("utf-8"),
            f"{stem}.csv",
            "text/csv; charset=utf-8",
        )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for index, offset in enumerate(range(0, len(rows), chunk_size), start=1):
            archive.writestr(
                f"{stem}_{index}.csv", render_rows(rows[offset : offset + chunk_size])
            )
    return buffer.getvalue(), f"{stem}.zip", "application/zip"
