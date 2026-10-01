"""Convert VeloBank account history locally without database or API access."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.velobank_import.exporter import render_csv  # noqa: E402
from services.velobank_import.parser import parse_pdf  # noqa: E402


def _write_csv(path: Path, content: str) -> None:
    """Publish a complete CSV without replacing an existing file."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.close()
            os.link(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Convert one PDF and print totals; return a nonzero status on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="VeloBank account-history PDF")
    parser.add_argument(
        "-o", "--output", type=Path, required=True, help="Output CSV (UTF-8, semicolon)"
    )
    parser.add_argument(
        "--account-name", default="VeloBank", help="Firefly asset account name"
    )
    parser.add_argument(
        "--own-account",
        action="append",
        default=[],
        metavar="IBAN=NAME",
        help="Other own account, e.g. PL123...=Main account; repeatable",
    )
    args = parser.parse_args(argv)
    own_accounts: dict[str, str] = {}
    for item in args.own_account:
        iban, separator, name = item.partition("=")
        iban = re.sub(r"\s", "", iban).upper().removeprefix("PL")
        if not separator or not re.fullmatch(r"\d{26}", iban) or not name.strip():
            parser.error("--own-account must be a Polish IBAN=Firefly account name")
        own_accounts["PL" + iban] = name.strip()
    if not args.account_name.strip():
        parser.error("--account-name cannot be empty")
    try:
        statement = parse_pdf(args.pdf)
        csv_content = render_csv(
            statement, account_name=args.account_name.strip(), own_accounts=own_accounts
        )
        # Parse everything before touching the output. Exclusive creation protects
        # an existing file, including the source PDF passed as output by mistake.
        _write_csv(args.output, csv_content)
    except (OSError, ValueError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1
    currencies = sorted({tx.currency for tx in statement.transactions})
    print(f"Exported {len(statement.transactions)} transactions to {args.output}")
    for currency in currencies:
        amounts = [
            tx.amount for tx in statement.transactions if tx.currency == currency
        ]
        debits = sum((-a for a in amounts if a < 0), Decimal("0"))
        credits = sum((a for a in amounts if a > 0), Decimal("0"))
        print(f"{currency}: debits {debits:.2f}; credits {credits:.2f}")
    print(
        "Generated IDs require the same complete set of identical operations per day. Review overlapping exports and own-account transfers before importing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
