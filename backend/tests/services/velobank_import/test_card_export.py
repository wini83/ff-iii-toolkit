import csv
import hashlib
import json
from io import StringIO
from uuid import uuid4

import pytest

from services.velobank_import.exporter import export_rows, render_csv
from services.velobank_import.parser import VeloBankStatement, normalize
from services.velobank_import.service import preview_data
from services.velobank_import.session import VeloBankPreviewStore
from tests.services.velobank_import.test_velobank import ACCOUNT, record


@pytest.mark.parametrize(
    "card_number, details, payee, description",
    [
        (
            "5125 **** **** 1234 ",
            "ANOPI SP. Z O.O., Sroda Wielkop, PL",
            "ANOPI SP. Z O.O.",
            "ANOPI SP. Z O.O., Sroda Wielkop, PL",
        ),
        (
            "5125 **** **** 1234 ",
            "Microsoft*Store, msbill.info, IE",
            "Microsoft*Store",
            "Microsoft*Store, msbill.info, IE",
        ),
        (
            "",
            "TEST HOLDER-Warszawa-MPS*VWFS\nUbezpieczenia-616",
            "MPS*VWFS Ubezpieczenia",
            "MPS*VWFS Ubezpieczenia, Warszawa",
        ),
        (
            "",
            "TEST HOLDER-Szczecin-Corona Coffee-616",
            "Corona Coffee",
            "Corona Coffee, Szczecin",
        ),
        (
            "",
            "TEST HOLDER-SZCZECIN-ZABKA ZC383 K.1\n616",
            "ZABKA ZC383 K.1",
            "ZABKA ZC383 K.1, SZCZECIN",
        ),
        (
            "",
            "TEST HOLDER-Amsterdam-Booking.com Hotel-528",
            "Booking.com Hotel",
            "Booking.com Hotel, Amsterdam",
        ),
        (
            "",
            "TEST HOLDER-POZNAN-VEMAT AUTOMATY 2- 616",
            "VEMAT AUTOMATY 2",
            "VEMAT AUTOMATY 2, POZNAN",
        ),
        (
            "",
            "TEST HOLDER-Poznan-CM Enel Kupiec Poznans-616",
            "CM Enel Kupiec Poznans",
            "CM Enel Kupiec Poznans, Poznan",
        ),
        ("", "TEST HOLDER-POZNAN-ESUS-616", "ESUS", "ESUS, POZNAN"),
        (
            "",
            "TEST HOLDER-Szczecin-KWIACIARNIA BEAUT83154-616",
            "KWIACIARNIA BEAUT83154",
            "KWIACIARNIA BEAUT83154, Szczecin",
        ),
        (
            "",
            "TEST HOLDER-Warszawa-Shop-with-hyphens-616",
            "Shop-with-hyphens",
            "Shop-with-hyphens, Warszawa",
        ),
        ("", "Shop, City, PL", "Shop", "Shop, City, PL"),
        (
            "1234 ",
            "SHOP-WITH-HYPHENS-616",
            "SHOP-WITH-HYPHENS-616",
            "SHOP-WITH-HYPHENS-616",
        ),
        ("", "Shop; test", "Shop; test", "Shop; test"),
    ],
)
def test_card_preview_and_csv(card_number, details, payee, description):
    raw = f"Operacja kartą  {card_number}na kwotę 12,34 PLN  w {details}"
    tx = record(raw)
    statement = VeloBankStatement(ACCOUNT, [tx])
    accounts = {"PL" + ACCOUNT: "Card"}
    entry = VeloBankPreviewStore().create(uuid4(), statement)
    preview = preview_data(entry, accounts)["preview"][0]
    exported = list(
        csv.DictReader(
            StringIO(render_csv(statement, account_name="Card", own_accounts=accounts)),
            delimiter=";",
        )
    )[0]
    assert preview["payee"] == exported["Payee"] == payee
    assert preview["description"] == exported["Description"] == description
    assert preview["destination_account"] == exported["Destination account"] == payee
    assert not preview["needs_review"]
    assert tx.description == raw
    # Freeze the v1 identifier contract: presentation changes must not alter it.
    key = json.dumps(
        [
            ACCOUNT,
            tx.date.isoformat(),
            tx.booking_date.isoformat(),
            f"{tx.amount:.2f}",
            tx.currency,
            normalize(raw),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected_id = f"velobank-v1-{hashlib.sha256(key.encode()).hexdigest()}-1"
    assert preview["id"] == exported["External ID"] == expected_id


@pytest.mark.parametrize(
    "raw, payee",
    [
        (
            "Przelew na rachunek: 98765432109876543210987654, Odbiorca: Test Person, Tytuł: Payment",
            "Test Person",
        ),
        ("Unrecognized operation", "Unknown counterparty"),
        ("Operacja kartą na kwotę 12,34 PLN w ", "Unknown counterparty"),
    ],
)
def test_non_card_and_incomplete_descriptions_are_preserved(raw, payee):
    statement = VeloBankStatement(ACCOUNT, [record(raw)])
    row = export_rows(statement, account_name="Card", own_accounts={})[0]
    assert row["Description"] == raw
    assert row["Payee"] == payee
