"""Read the existing Alior/BLIK CSV format without retaining the upload."""

import csv
import io
import re
from collections.abc import Iterator
from datetime import date

from services.csv_reader import AliorDialect, parse_amount, parse_pl_date
from services.reconciliation.models import BankLine, StatementPeriod

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
REQUIRED = {
    "Data transakcji",
    "Kwota w walucie rachunku",
    "Waluta rachunku",
    "Szczegóły transakcji",
}
DATE_VALUE = r"(?:[0-9]{4}[-.][0-9]{2}[-.][0-9]{2}|[0-9]{2}[-.][0-9]{2}[-.][0-9]{4})"
DECLARED_RANGE = re.compile(
    rf"(?:^|;)\s*(?:wyciąg za okres|historia operacji za okres|okres(?: wyciągu)?|zakres dat|data od)"
    rf"\s*[:;]?\s*"
    rf"(?:od\s*(?:dnia\s*)?[:;]?\s*)?({DATE_VALUE})\s*"
    rf"(?:(?:;\s*)?(?:data\s+)?do\s*(?:dnia\s*)?[:;]?|[–—;-])\s*({DATE_VALUE})(?![0-9])",
    re.IGNORECASE,
)


class StatementLine(BankLine):
    counterparty_account: str = ""


def parse_statement(content: bytes) -> list[StatementLine]:
    rows, _ = _read_statement(content)
    return rows


def statement_period(content: bytes) -> StatementPeriod:
    rows, metadata = _read_statement(content)
    first = min(row.date for row in rows)
    last = max(row.date for row in rows)
    declared = DECLARED_RANGE.search(" ".join(";".join(metadata).split()))
    if declared:
        try:
            start, end = (
                date.fromisoformat(value.replace(".", "-"))
                if value[4] in "-."
                else parse_pl_date(value.replace(".", "-"))
                for value in declared.groups()
            )
        except ValueError:
            pass
        else:
            if start <= first <= last <= end:
                return StatementPeriod(date_from=start, date_to=end, source="metadata")
    return StatementPeriod(date_from=first, date_to=last, source="transactions")


def _read_statement(content: bytes) -> tuple[list[StatementLine], list[str]]:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload a non-empty CSV file of at most 2 MiB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV must be encoded as UTF-8") from None
    records = _records(text)
    _, first = next(records, (0, []))
    if REQUIRED.issubset(first):
        header, metadata = first, []
    else:
        _, header = next(records, (0, []))
        metadata = first
    if not REQUIRED.issubset(header) or len(set(header)) != len(header):
        raise ValueError("Unsupported CSV headers; use the Alior/BLIK export format")

    result: list[StatementLine] = []
    nonempty_records = ((line, values) for line, values in records if values)
    for number, (line, values) in enumerate(nonempty_records, start=1):
        if len(values) <= len(header) and not any(value.strip() for value in values):
            continue
        location = f"CSV file line {line} (record {number})"
        if len(values) != len(header):
            raise ValueError(
                f"Invalid columns at {location}: expected {len(header)}, found {len(values)}"
            )
        if len(result) >= MAX_ROWS:
            raise ValueError("CSV exceeds 2000 transactions")
        row = dict(zip(header, values, strict=True))
        result.append(_parse_transaction(row, number, location))
    if not result:
        raise ValueError("CSV contains no transactions")
    return result, metadata


def _records(text: str) -> Iterator[tuple[int, list[str]]]:
    reader = csv.reader(io.StringIO(text, newline=""), dialect=AliorDialect)
    while True:
        line = reader.line_num + 1
        try:
            values = next(reader)
        except StopIteration:
            return
        except csv.Error:
            raise ValueError(
                f"Malformed CSV record starting at file line {line}"
            ) from None
        yield line, values


def _parse_transaction(
    row: dict[str, str], number: int, location: str
) -> StatementLine:
    try:
        field = "Kwota w walucie rachunku"
        amount = parse_amount(row[field])
        if not amount.is_finite():
            raise ValueError()

        field = "Waluta rachunku"
        currency = row[field].strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError()

        field = "Szczegóły transakcji"
        description = row[field]
        trimmed_description = description.strip()
        if not trimmed_description or len(trimmed_description) > 2000:
            raise ValueError()

        booking = row.get("Data księgowania", "").strip()
        field = "Data księgowania" if booking else "Data transakcji"
        day = parse_pl_date(row[field])
    except ValueError:
        raise ValueError(f'Invalid value in "{field}" at {location}') from None

    own = counterparty = ""
    if not amount.is_zero():
        own = row.get(
            "Numer rachunku nadawcy" if amount < 0 else "Numer rachunku odbiorcy",
            "",
        ).strip()
        counterparty = row.get(
            "Numer rachunku odbiorcy" if amount < 0 else "Numer rachunku nadawcy",
            "",
        ).strip()
    return StatementLine(
        row_id=number,
        date=day,
        amount=str(amount),
        currency=currency,
        description=description,
        own_account=own,
        counterparty_account=counterparty,
    )
