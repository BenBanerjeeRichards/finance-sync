from typing_extensions import Iterable
from pydantic import BaseModel
from datetime import date, datetime
from decimal import Decimal
import re
import logging
from model import GcSantanderTransaction as GcSantanderTransaction
import uuid

from typing import Optional

TRANSFER = "TRANSFTER"
GIRO = "GIRA"
CARD = "CARD"

class SantanderTransaction(BaseModel):
    id: str
    date: date
    description: str
    amount: Decimal
    balance: Optional[Decimal] = None # We don't really need this anyway
    reference: Optional[str] = None
    account_name: Optional[str] = None
    type: Optional[str] = None


class ParsedDescription(BaseModel):
    type: Optional[str] = None
    reference: Optional[str] =  None
    account_name: Optional[str] = None


def _parse_description(description: str) -> ParsedDescription | None:
    match = re.search(r'DIRECT DEBIT PAYMENT TO (.*) REF (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'TRANSFER TO (.*) REFERENCE (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'FASTER PAYMENTS RECEIPT REF\.?(.*) FROM (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(1), account_name=match.group(2))

    match = re.search(r'BILL PAYMENT VIA FASTER PAYMENT TO (.*) REFERENCE (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'BILL PAYMENT VIA FASTER PAYMENT TO (.*) REFERENCE (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'Third party payment made via Faster Payment to (.*) Reference (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'THIRD PARTY PAYMENT MADE VIA FASTER PAYMENT TO (.*) REFERENCE (.*) SANTANDER REFERENCE.*', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'BANK GIRO CREDIT REF (.*)', description)
    if match:
        return ParsedDescription(type=GIRO, reference=match.group(1))

    match = re.search(r'BILL PAYMENT FROM (.*), REFERENCE (.*)', description)
    if match:
        return ParsedDescription(type=TRANSFER, reference=match.group(2), account_name=match.group(1))

    match = re.search(r'CARD PAYMENT TO (.*)   ON .*', description)
    if match:
        return ParsedDescription(type=CARD, reference=match.group(1), account_name=match.group(1))

    match = re.search(r'POST OFFICE CASH WITHDRAWAL', description)
    if match:
        return ParsedDescription(type=TRANSFER, account_name="POST OFFICE", reference="POST OFFICE")

    match = re.search(r'(.*) \(VIA APPLY PAY\)', description)
    if match:
        return ParsedDescription(type=CARD, account_name=match.group(1), reference=None)

    return None

# Load from an CSV export from santander
def parse_csv(contents: str) -> Iterable[SantanderTransaction]:
    for block in contents.split("\n\n")[4:]:
        block_lines = [line.split(":")[1].replace("†", "") for line in block.split("\n") if line.strip()]
        if len(block_lines) != 4:
            logging.warning("Skipping santander transaction - not 4 lines long: %s", block_lines)
        date_line, description_line, amount_line, balance_line = block_lines
        parsed_description = _parse_description(description_line)
        if not parsed_description:
            logging.warning("Failed to parse description for transaction on date %s: %s", date_line, description_line)
            account_name = None
            type = None
            ref = None
        else:
            ref = parsed_description.reference
            type = parsed_description.type
            account_name = parsed_description.account_name
        balance = Decimal(balance_line)
        amount = Decimal(amount_line)
        date = datetime.strptime(date_line, "%d/%m/%Y").date()
        yield SantanderTransaction(id=str(uuid.uuid4()), date=date, description=description_line, amount=amount, balance=balance,
            reference=ref, account_name=account_name, type=type)

# Convert from GoCardless
def from_gc(tx: GcSantanderTransaction) -> SantanderTransaction:
    parsed_description = _parse_description(tx.description)
    if tx.counterparty_name:
        account_name = tx.counterparty_name
    else:
        account_name = None if not parsed_description else parsed_description.account_name
    type = None if not parsed_description else parsed_description.type
    ref = None if not parsed_description else parsed_description.reference
    if not parsed_description:
        logging.warning("Failed to parse description for transaction for transaction %s", tx)
    if not tx.date:
        raise ValueError("Invalid transaction - no date provided %s", tx)
    return SantanderTransaction(id=tx.transaction_id, date=tx.date, description=tx.description, amount=tx.amount, balance=None,
        reference=ref, account_name=account_name, type=type)
