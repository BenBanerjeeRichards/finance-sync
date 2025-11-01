from beancount_sync.parser import printer

from typing import Iterable
from santander import SantanderTransaction


def process_santander(config: dict, transaction_iter: Iterable[SantanderTransaction]) -> str:
    res = ""
    transactions = list(reversed(list(transaction_iter)))
    for tx, next_tx in zip(transactions, transactions[1:] + [None]):
        bean_tx = santander_to_beancount_tx(config, tx)
        if bean_tx:
            res += printer.format_entry(bean_tx) + "\n"
    return res

