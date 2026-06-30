import logging

from beancount.core.data import Meta, Posting, Transaction
from beancount.core.position import Amount, Decimal
from typing_extensions import Literal
import datetime


def create_amount(pence: int) -> Amount:
    return Amount.from_string(str(Decimal(pence) / 100) + " GBP")


def create_amount_from_decimal(amount: Decimal) -> Amount:
    return Amount.from_string(str(amount) + " GBP")


def create_transaction(date: datetime.date, payee: str, credit_account: str, debit_account: str, amount: Decimal,
                       narration: str = "",
                       flag: Literal["!", "*"] = "*",
                       meta: dict | None = None):
    if amount <= 0:
        # amount is positive as we use the credit and debit accounts to determine flow
        logging.error("attempted to create transaction with negative amount: credit=%s debit=%s amount=%s",
                      credit_account, debit_account, amount)
        raise ValueError("Amount must be greater than 0")

    credit_posting = new_posting(account=credit_account,
                                 units=create_amount_from_decimal(-1 * amount))
    debit_posting = new_posting(account=debit_account,
                                units=create_amount_from_decimal(amount))
    return new_transaction(date, flag, [credit_posting, debit_posting], payee,
                           narration, meta=meta or {})


def new_posting(account, units=None, cost=None, price=None, flag=None, meta=None):
    return Posting(account=account,
                   units=units,
                   cost=cost,
                   price=price,
                   flag=flag,
                   meta=meta)


def new_transaction(date, flag, postings, payee='', narration='', tags=frozenset(), links=frozenset(), meta=Meta()):
    return Transaction(date=date,
                       flag=flag,
                       postings=postings,
                       payee=payee,
                       narration=narration,
                       tags=tags,
                       links=links,
                       meta=meta)


def _comparable_metadata(meta: Meta | None) -> dict:
    if meta is None:
        return {}
    disallowed_keys = ['filename', 'lineno', '__tolerances__', 'external_id']
    allowed_keys = [k for k in meta.keys() if k not in disallowed_keys]
    return {k: meta.get(k) for k in allowed_keys}


def _compare_postings(old: Posting, to: Posting) -> dict:
    diff = {}
    if old.account != to.account:
        diff["account"] = {'old': old.account, 'new': to.account}
    if old.units != to.units:
        diff["units"] = {'old': old.units, 'new': to.units}
    if old.cost != to.cost:
        diff["units"] = {'old': old.cost, 'new': to.cost}
    if old.price != to.price:
        diff["price"] = {'old': old.price, 'new': to.price}
    if old.flag != to.flag:
        diff["flag"] = {'old': old.flag, 'new': to.flag}
    return diff


def transactions_equal(old: Transaction | None, to: Transaction) -> dict:
    diff = {}
    if old is None and to is not None:
        return {"transaction": {"old": None, "new": to}}

    if old.date != to.date:
        diff["date"] = {'old': old.date, 'new': to.date}
    if old.flag != to.flag:
        diff["flag"] = {'old': old.flag, 'new': to.flag}
    old_payee = old.payee or ''
    to_payee = to.payee or ''
    if old_payee != to_payee:
        diff["payee"] = {'old': old_payee, 'new': to_payee}
    if old.narration != to.narration:
        diff["narration"] = {'old': old.narration, 'new': to.narration}
    if set(old.tags) != set(to.tags):
        diff["tags"] = {'old': old.tags, 'new': to.tags}
    if old.links != to.links:
        diff["links"] = {'old': old.links, 'new': to.links}

    old_meta = _comparable_metadata(old.meta)
    new_meta = _comparable_metadata(to.meta)
    if old_meta != new_meta:
        diff["meta"] = {'old': old_meta, 'new': new_meta}

    if len(old.postings) > 2 or len(to.postings) > 2:
        # Right now we only create one posting for credit and one for debit
        raise RuntimeError("Invalid beancount transaction: more than 2 postings found")

    if len(old.postings) != len(to.postings):
        diff["postings_len"] = {"old": len(old.postings), "new": len(to.postings)}
    else:
        for i, old_posting in enumerate(old.postings):
            to_posting = to.postings[i]
            d = _compare_postings(old_posting, to_posting)
            if d != {}:
                if "posting" not in diff:
                    diff["posting"] = {}
                diff["posting"][i] = d

    return diff
