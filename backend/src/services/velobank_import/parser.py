"""Read the five-column, text-based VeloBank account-history layout."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber


class VeloBankParseError(ValueError):
    """The document cannot be converted without risking missing operations."""


def normalize(value: str) -> str:
    """Join wrapped descriptions and normalize PDF whitespace."""
    return " ".join(value.split())


def _header(value: str) -> str:
    return normalize(value).upper().translate(str.maketrans("ĄĆĘŁŃÓŚŹŻ", "ACELNOSZZ"))


HEADERS = [
    "DATA TRANSAKCJI",
    "DATA KSIEGOWANIA",
    "OPIS TRANSAKCJI",
    "KWOTA TRANSAKCJI",
    "SALDO PO TRANSAKCJI",
]
DATE_PATTERN = re.compile(r"\d{2}\.\d{2}\.\d{4}")
ACCOUNT_PATTERN = re.compile(r"NUMER RACHUNKU:\s*((?:\d[\s]*){26})(?!\d)")
AMOUNT_PATTERN = re.compile(r"(-?\d+(?: \d{3})*,\d{2}) ([A-Z]{3})")


@dataclass(frozen=True, slots=True)
class VeloBankTransaction:
    """A booked operation, preserving its original description and both dates."""

    date: date
    booking_date: date
    description: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class VeloBankStatement:
    """Account information and all operations found in a statement."""

    account: str
    transactions: list[VeloBankTransaction]


def _transaction(cells: list[str]) -> VeloBankTransaction:
    amount_match = AMOUNT_PATTERN.fullmatch(cells[3])
    if not amount_match or not cells[2]:
        raise VeloBankParseError("Missing description or invalid transaction amount.")
    try:
        return VeloBankTransaction(
            date=datetime.strptime(cells[0], "%d.%m.%Y").date(),
            booking_date=datetime.strptime(cells[1], "%d.%m.%Y").date(),
            description=cells[2],
            amount=Decimal(amount_match[1].replace(" ", "").replace(",", ".")),
            currency=amount_match[2],
        )
    except ValueError as exc:
        raise VeloBankParseError("Invalid transaction or booking date.") from exc


def parse_pdf(path: Path) -> VeloBankStatement:
    """Extract every row or fail with its page number.

    Args:
        path: Text-based VeloBank account-history PDF with five columns.

    Returns:
        Statement containing the account number and every operation in PDF order.

    Raises:
        VeloBankParseError: Unknown layout, scans, missing account, or invalid rows.
    """
    transactions: list[VeloBankTransaction] = []
    account = ""
    try:
        document = pdfplumber.open(path)
    except Exception as exc:
        raise VeloBankParseError(
            "Cannot open PDF (invalid, encrypted or unreadable file)."
        ) from exc
    with document as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            found = ACCOUNT_PATTERN.search(text)
            if found:
                current_account = re.sub(r"\s", "", found[1])
                if account and current_account != account:
                    raise VeloBankParseError("The PDF contains different accounts.")
                account = current_account
            header_table = None
            for table in page.find_tables():
                rows = table.extract()
                if rows and [_header(c or "") for c in rows[0]] == HEADERS:
                    header_table = table
                    break
            if header_table is None:
                raise VeloBankParseError(
                    f"Page {page_number}: expected VeloBank table header not found "
                    "(scanned PDFs and other layouts are unsupported)."
                )
            boundaries = sorted(
                {x for cell in header_table.cells for x in (cell[0], cell[2])}
            )
            if len(boundaries) != 6:
                raise VeloBankParseError(f"Page {page_number}: invalid column layout.")
            header_bottom = max(
                cell[3] for cell in header_table.rows[0].cells if cell is not None
            )
            # The body has horizontal rules but no vertical borders. Extend the
            # five header columns across its rows instead of trusting auto detection.
            cropped = page.crop(
                (boundaries[0], header_table.bbox[1], boundaries[-1], page.height)
            )
            rows = cropped.extract_tables(
                {
                    "vertical_strategy": "explicit",
                    "explicit_vertical_lines": boundaries,
                    "horizontal_strategy": "lines",
                }
            )
            page_count = 0
            for table_rows in rows:
                for row in table_rows:
                    cells = [normalize(c or "") for c in row]
                    if cells == HEADERS or [_header(c) for c in cells] == HEADERS:
                        continue
                    if not any(cells):
                        continue
                    if len(cells) != 5:
                        raise VeloBankParseError(f"Page {page_number}: invalid row.")
                    if (
                        not cells[0]
                        and not cells[1]
                        and cells[2]
                        and not cells[3]
                        and not cells[4]
                        and transactions
                    ):
                        # A description can continue on the next page.
                        previous = transactions[-1]
                        transactions[-1] = replace(
                            previous,
                            description=normalize(
                                previous.description + " " + cells[2]
                            ),
                        )
                        continue
                    try:
                        transactions.append(_transaction(cells))
                    except VeloBankParseError as exc:
                        raise VeloBankParseError(
                            f"Page {page_number}, row {page_count + 1}: {exc}"
                        ) from exc
                    page_count += 1
            # Cross-check date anchors so a missing final border cannot silently
            # drop an operation from the extracted tables.
            date_count = sum(
                1
                for word in page.extract_words()
                if boundaries[0] <= word["x0"] < boundaries[1]
                and word["top"] >= header_bottom
                and DATE_PATTERN.fullmatch(word["text"])
            )
            if page_count != date_count:
                raise VeloBankParseError(
                    f"Page {page_number}: found {date_count} date rows but parsed "
                    f"{page_count}; refusing incomplete output."
                )
    if not account or not transactions:
        raise VeloBankParseError("No account number or transactions found.")
    return VeloBankStatement(account=account, transactions=transactions)
