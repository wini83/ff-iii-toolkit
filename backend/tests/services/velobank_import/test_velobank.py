import csv
import importlib.util
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from services.velobank_import.exporter import render_csv
from services.velobank_import.parser import (
    VeloBankParseError,
    VeloBankStatement,
    VeloBankTransaction,
    parse_pdf,
)

ACCOUNT = "12345678901234567890123456"
OTHER_ACCOUNT = "98765432109876543210987654"


def make_pdf(path, pages, *, final_border=True, account=ACCOUNT):
    """Build synthetic, anonymous five-column PDFs without an extra dependency."""
    bounds = [25, 90, 160, 440, 510, 575]
    headers = [
        "DATA\nTRANSAKCJI",
        "DATA\nKSIEGOWANIA",
        "OPIS TRANSAKCJI",
        "KWOTA\nTRANSAKCJI",
        "SALDO PO\nTRANSAKCJI",
    ]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for rows in pages:
        commands = []

        def text(x, y, value, commands=commands):
            escaped = (
                value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            )
            commands.append(f"BT /F1 6 Tf {x} {y} Td ({escaped}) Tj ET")

        def line(x1, y1, x2, y2, commands=commands):
            commands.append(f"{x1} {y1} m {x2} {y2} l S")

        text(25, 790, "Historia rachunku")
        text(25, 775, "NUMER RACHUNKU: " + account)
        line(25, 740, 575, 740)
        line(25, 715, 575, 715)
        for x in bounds:
            line(x, 740, x, 715)
        for x, header in zip(bounds, headers, strict=False):
            for n, part in enumerate(header.split("\n")):
                text(x + 2, 732 - n * 8, part)
        y = 715
        for index, cells in enumerate(rows):
            height = max(len(cell.split("\n")) for cell in cells) * 10 + 12
            for x, cell in zip(bounds, cells, strict=False):
                for n, part in enumerate(cell.split("\n")):
                    text(x + 2, y - 10 - n * 10, part)
            y -= height
            if final_border or index < len(rows) - 1:
                line(25, y, 575, y)
        stream = "\n".join(commands).encode("ascii")
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    objects[1] = (
        f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(f'{n} 0 R' for n in page_ids)}] >>".encode()
    )
    payload = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    payload += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    payload += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(payload)


def row(description="Shop\nsecond line", amount="-12,34 PLN"):
    return ["01.09.2026", "02.09.2026", description, amount, "0,00"]


def record(description="Shop", amount="-12.34"):
    return VeloBankTransaction(
        date(2026, 9, 1), date(2026, 9, 2), description, Decimal(amount), "PLN"
    )


def test_parses_repeated_headers_multiline_and_multiple_pages(tmp_path):
    path = tmp_path / "statement.pdf"
    make_pdf(path, [[row()], [row("Payment", "2 200,00 PLN")]])
    result = parse_pdf(path)
    assert result.account == ACCOUNT
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "Shop second line"
    assert result.transactions[0].date == date(2026, 9, 1)
    assert result.transactions[0].booking_date == date(2026, 9, 2)
    assert result.transactions[1].amount == Decimal("2200.00")


def test_description_continues_after_page_break(tmp_path):
    path = tmp_path / "statement.pdf"
    make_pdf(
        path,
        [
            [row("First part")],
            [["", "", "continued description", "", ""], row("Next operation")],
        ],
    )
    result = parse_pdf(path)
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "First part continued description"


@pytest.mark.parametrize(
    "bad_row",
    [
        row(amount="invalid PLN"),
        ["31.02.2026", "02.09.2026", "Shop", "-1,00 PLN", "0,00"],
        row(description=""),
    ],
)
def test_rejects_invalid_operations(tmp_path, bad_row):
    path = tmp_path / "bad.pdf"
    make_pdf(path, [[bad_row]])
    with pytest.raises(VeloBankParseError, match="Page 1"):
        parse_pdf(path)


def test_missing_bottom_rule_does_not_silently_drop_last_row(tmp_path):
    path = tmp_path / "bad.pdf"
    make_pdf(path, [[row(), row("Last operation")]], final_border=False)
    with pytest.raises(VeloBankParseError, match="refusing incomplete output"):
        parse_pdf(path)


def test_ids_stable_for_overlap_and_preserve_identical_operations():
    first = record()
    second = replace(first, description="Another shop")
    whole = VeloBankStatement(ACCOUNT, [first, first, second])
    rows = list(
        csv.DictReader(
            StringIO(render_csv(whole, account_name="Card", own_accounts={})),
            delimiter=";",
        )
    )
    overlap = list(
        csv.DictReader(
            StringIO(
                render_csv(
                    VeloBankStatement(ACCOUNT, [first, first]),
                    account_name="Card",
                    own_accounts={},
                )
            ),
            delimiter=";",
        )
    )
    assert len({r["External ID"] for r in rows}) == 3
    assert [r["External ID"] for r in rows[:2]] == [r["External ID"] for r in overlap]
    assert rows[0]["Amount"] == "-12.34"
    assert rows[0]["Source account"] == "Card"


def test_transfer_mapping_and_csv_quoting():
    credit = record(
        f"Przelew z rachunku: {OTHER_ACCOUNT}, Nadawca: Test Person, Tytuł: card payment",
        "2200.00",
    )
    debit = record("Operacja kartą 1234 **** na kwotę 12,34 PLN w Shop; test")
    rows = list(
        csv.DictReader(
            StringIO(
                render_csv(
                    VeloBankStatement(ACCOUNT, [credit, debit]),
                    account_name="Card",
                    own_accounts={"PL" + OTHER_ACCOUNT: "Main account"},
                )
            ),
            delimiter=";",
        )
    )
    assert rows[0]["Source account"] == "Main account"
    assert rows[0]["Destination account"] == "Card"
    assert rows[0]["Source IBAN"] == "PL" + OTHER_ACCOUNT
    assert rows[1]["Payee"] == "Shop; test"


def test_cli_preserves_existing_output_and_reports_totals(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location(
        "velobank_cli", Path(__file__).resolve().parents[3] / "cli" / "velobank.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "statement.pdf"
    make_pdf(path, [[row()]])
    output = tmp_path / "output.csv"
    args = [str(path), "-o", str(output)]
    assert module.main(args) == 0
    assert "debits 12.34; credits 0.00" in capsys.readouterr().out
    original = output.read_bytes()
    assert module.main(args) == 1
    assert output.read_bytes() == original


def test_cli_failure_creates_no_output(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "velobank_cli", Path(__file__).resolve().parents[3] / "cli" / "velobank.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "statement.pdf"
    make_pdf(path, [[row(amount="bad")]])
    output = tmp_path / "output.csv"
    assert module.main([str(path), "-o", str(output)]) == 1
    assert not output.exists()


def test_invalid_pdf_is_reported_as_conversion_error(tmp_path):
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"not a PDF")
    with pytest.raises(VeloBankParseError, match="Cannot open PDF"):
        parse_pdf(path)


def test_missing_account_is_rejected(tmp_path):
    path = tmp_path / "missing-account.pdf"
    make_pdf(path, [[row()]], account="")
    with pytest.raises(VeloBankParseError, match="No account number"):
        parse_pdf(path)
