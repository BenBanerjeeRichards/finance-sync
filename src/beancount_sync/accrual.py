from beancount_sync.beancount import Beancount
from beancount_sync.beancount_sync import BeancountTransaction
from beancount_sync.beancount_util import new_transaction, new_posting, create_amount_from_decimal, create_transaction
from model import Config, AccrualConfig
import logging
from decimal import Decimal
from dateutil.relativedelta import relativedelta

# Allows for splitting out a quarterly/yearly etc bill into monthly transactions
# Right now only handles things invoiced in arrears

# The actual payment of an invoice, credits cash and debits liability
VALUE_SETTLEMENT = "settlement"
# Estimated liability based on previous settlement amounts and billing period
VALUE_PROVISIONAL_LIABILITY = "provisional_liability"
# Actual liability computed from settlement
VALUE_LIABILITY = "liability"


class BeancountAccruals:

    def __init__(self, beancount: Beancount, config: Config):
        self.beancount = beancount
        self.config = config

    def run_accruals(self):
        for rule in self.config.accruals:
            self.process_accrual(rule)

    def process_accrual(self, rule: AccrualConfig):
        logging.info("Calculating accruals for rule %s", rule.metadata_key)
        settlements = self.beancount.find_all_by_metadata(rule.metadata_key, VALUE_SETTLEMENT)
        liabilities = self.beancount.find_all_by_metadata(rule.metadata_key, VALUE_LIABILITY)
        if not settlements:
            # If we have not yet had any actual transactions, we have nothing to base the liabilities on
            logging.warning("No settlements found for rule %s, can't compute any liabilities", rule.metadata_key)
            return

        new_transactions: list[BeancountTransaction] = []

        for settlement in settlements:
            settlement_key = settlement.meta.get("external_id")
            liability_amounts = split_money_decimal(abs(settlement.postings[0].units.number), rule.settlement_months)
            if not settlement_key:
                logging.error("settlement has no external id %s", settlement)
                continue

            liability_months = [(settlement.date - relativedelta(months=n + 1)).replace(day=1) for n in
                                range(rule.settlement_months)]

            for i, liability_date in enumerate(liability_months):
                liability_key = f"{settlement_key}-{liability_date.isoformat()}"
                if [l for l in liabilities if l.meta.get("external_id") == liability_key]:
                    continue

                tx = BeancountTransaction(external_id=liability_key, tx_date=liability_date,
                                          credit_account=rule.liability_account, debit_account=rule.expense_account,
                                          payee=settlement.payee, description=settlement.narration, flagged=False,
                                          ledger_metadata={}, source="accrual", amount=abs(liability_amounts[i]),
                                          metadata={
                                              rule.metadata_key: VALUE_LIABILITY
                                          })
                new_transactions.append(tx)

        with self.beancount.transaction() as beancount_tx:
            for tx in new_transactions:
                beancount_tx.create_or_update_transaction(self.config.accrualBeanFileName, tx)



def split_money_decimal(total_amount, n):
    total = Decimal(str(total_amount)).quantize(Decimal('0.01'))
    total_cents = int(total * 100)
    base_cents, remainder_cents = divmod(total_cents, n)
    parts = [Decimal(base_cents) / 100 for _ in range(n)]
    for i in range(remainder_cents):
        parts[i] += Decimal('0.01')
    return parts
