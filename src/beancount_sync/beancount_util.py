from beancount.core.data import Meta, Posting, Transaction
from beancount.core.position import Amount, Decimal


def create_amount(pence: int) -> Amount:
    return Amount.from_string(str(Decimal(pence) / 100) + " GBP")


def create_amount_from_decimal(amount: Decimal) -> Amount:
    return Amount.from_string(str(amount) + " GBP")


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


def transactions_equal(a: Transaction, b: Transaction) -> bool:
    # this is a pain: we want to compare a and b ignoring metadata
    # named tuples are immutable, so construct new transactions without metadata
    if (a is None and b is not None) or (b is None and a is not None):
        return False

    def clean_posting(old: Posting) -> Posting:
        return Posting(account=old.account, units=old.units, cost=old.cost, price=old.price, flag=old.flag, meta=None)

    def clean_transaction(old: Transaction) -> Transaction:
        return Transaction(date=old.date, flag=old.flag, payee='' if old.payee is None else old.payee,
                           narration='' if old.narration is None else old.narration, tags=old.tags, links=old.links,
                           postings=[clean_posting(p) for p in old.postings], meta={})

    return clean_transaction(a) == clean_transaction(b)
