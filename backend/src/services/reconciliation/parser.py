"""Read the existing Alior/BLIK CSV format without retaining the upload."""

import csv
import io
from decimal import Decimal

from services.csv_reader import parse_amount, parse_pl_date, read_alior_csv
from services.reconciliation.models import BankLine

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
REQUIRED = {
    "Data transakcji",
    "Kwota w walucie rachunku",
    "Waluta rachunku",
    "Szczegóły transakcji",
}


def parse_statement(content: bytes) -> list[BankLine]:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload a non-empty CSV file of at most 2 MiB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV must be encoded as UTF-8") from None
    try:
        reader = read_alior_csv(
            io.StringIO(text, newline=""), expected_headers=REQUIRED
        )
        header = reader.fieldnames or []
        if not set(header) >= REQUIRED or len(set(header)) != len(header):
            raise ValueError(
                "Unsupported CSV headers; use the Alior/BLIK export format"
            )
        result: list[BankLine] = []
        for number, row in enumerate(reader, start=1):
            if None not in row and all(
                value is None or not value.strip() for value in row.values()
            ):
                continue
            if len(result) >= MAX_ROWS or None in row or None in row.values():
                raise ValueError("CSV has malformed rows or exceeds 2000 transactions")
            try:
                amount = parse_amount(row["Kwota w walucie rachunku"])
                currency = row["Waluta rachunku"].strip().upper()
                description = row["Szczegóły transakcji"].strip()
                booking = row.get("Data księgowania", "").strip()
                day = parse_pl_date(booking or row["Data transakcji"])
                if (
                    not amount.is_finite()
                    or amount == Decimal(0)
                    or not description
                    or len(description) > 2000
                ):
                    raise ValueError()
                if (
                    len(currency) != 3
                    or not currency.isascii()
                    or not currency.isalpha()
                ):
                    raise ValueError()
            except ValueError, KeyError:
                raise ValueError(f"Invalid transaction at CSV row {number}") from None
            own = row.get(
                "Numer rachunku nadawcy" if amount < 0 else "Numer rachunku odbiorcy",
                "",
            ).strip()
            result.append(
                BankLine(
                    row_id=number,
                    date=day,
                    amount=str(amount),
                    currency=currency,
                    description=description,
                    own_account=own,
                )
            )
    except csv.Error:
        raise ValueError("Malformed or empty CSV") from None
    if not result:
        raise ValueError("CSV contains no transactions")
    return result
