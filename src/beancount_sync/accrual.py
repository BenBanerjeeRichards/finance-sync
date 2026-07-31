from beancount_sync.beancount import Beancount
from beancount_sync.beancount_sync import SimpleLedgerTransaction
from ledger.ledger_service import LedgerService
from main import Session
from model import Config, AccrualConfig
import logging
from decimal import Decimal
from dateutil.relativedelta import relativedelta
import datetime

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
        with Session.begin() as session:
            for rule in self.config.accruals:
                self.process_accrual(session, rule)

    def process_accrual(self, session: "Session", rule: AccrualConfig):
        logging.info("Calculating accruals for rule %s", rule.metadata_key)
        settlements = self.beancount.find_all_by_metadata_by_date_desc(rule.metadata_key, VALUE_SETTLEMENT)
        provisional_liabilities = self.beancount.find_all_by_metadata_by_date_desc(rule.metadata_key,
                                                                                   VALUE_PROVISIONAL_LIABILITY)
        if not settlements:
            # If we have not yet had any actual transactions, we have nothing to base the liabilities on
            logging.warning("No settlements found for rule %s, can't compute any liabilities", rule.metadata_key)
            return

        settlement_transactions: list[SimpleLedgerTransaction] = []

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
                credit_account_id = LedgerService.get_account_by_full_name(session, rule.liability_account).id
                expense_account_id = LedgerService.get_account_by_full_name(session, rule.expense_account).id
                tx = SimpleLedgerTransaction(external_id=liability_key, tx_date=liability_date,
                                             credit_account_id=credit_account_id, debit_account_id=expense_account_id,
                                             payee=settlement.payee,
                                             description=f"{rule.name} - incurred liability",
                                             flagged=False,
                                             ledger_metadata={
                                              rule.metadata_key: VALUE_LIABILITY
                                          }, source="accrual", amount=abs(liability_amounts[i]),
                                             metadata={})
                settlement_transactions.append(tx)

        with self.beancount.transaction() as beancount_tx:
            for tx in settlement_transactions:
                beancount_tx.create_or_update_transaction(self.config.accrualBeanFileName, tx)

        settlements = self.beancount.find_all_by_metadata_by_date_desc(rule.metadata_key, VALUE_SETTLEMENT)
        if not settlements:
            logging.warning("(provisional) no settlements found for rule %s", rule.name)
            return
        most_recent_settlement = settlements[0]
        provisional_transactions = []

        diff = relativedelta(datetime.date.today(), most_recent_settlement.date)
        months_since_last_settlement = (diff.years * 12) + diff.months + 1  # 1 to include current month

        if months_since_last_settlement <= 0:
            logging.info("skipping provisional liabilities for %s due to no months since last settlement %s", rule.name,
                         most_recent_settlement)
            return

        # we include the last settlement month in case settlement occurs often doesn't include that month
        # this would lead to a gap
        provisional_liability_months = [(most_recent_settlement.date + relativedelta(months=n)).replace(day=1) for n in
                                        range(months_since_last_settlement)]

        # Take 10% more than the highest from recent transactions to be slightly pessimistic
        max_recent = max([abs(s.postings[0].units.number) for s in settlements[:4]])
        estimated_amount = Decimal(str(max_recent)) * Decimal('1.10')
        estimated_liability = split_money_decimal(estimated_amount, rule.settlement_months)[0]
        logging.info("liability %s: computing provisional liabilities for month %s (amount %s)", rule.name,
                     provisional_liability_months, estimated_liability)
        for i, liability_date in enumerate(provisional_liability_months):
            credit_account_id = LedgerService.get_account_by_full_name(session, rule.liability_account).id
            expense_account_id = LedgerService.get_account_by_full_name(session, rule.expense_account).id

            liability_key = f"provisional-{most_recent_settlement.meta["external_id"]}-{liability_date.isoformat()}"
            tx = SimpleLedgerTransaction(external_id=liability_key, tx_date=liability_date,
                                         credit_account_id=credit_account_id, debit_account_id=expense_account_id,
                                         payee=most_recent_settlement.payee,
                                         description=f"{rule.name} - provisional liability",
                                         flagged=False,
                                         ledger_metadata={
                                          rule.metadata_key: VALUE_PROVISIONAL_LIABILITY
                                      }, source="accrual", amount=abs(estimated_liability))
            provisional_transactions.append(tx)

        # Find any provisional liabilities that exist in journal but not created here
        # These should be deleted to ensure this process is idempotent
        new_transaction_ids = [tx.external_id for tx in provisional_transactions]
        existing_provisional_ids = [pl.meta["external_id"] for pl in provisional_liabilities]
        delete_tx_ids = set(existing_provisional_ids) - set(new_transaction_ids)
        if delete_tx_ids:
            logging.warning("%s: deleting transactions: %s", rule.name, delete_tx_ids)

        with self.beancount.transaction() as beancount_tx:
            for tx in provisional_transactions:
                beancount_tx.create_or_update_transaction(self.config.accrualBeanFileName, tx)
            for tx_id in delete_tx_ids:
                beancount_tx.delete_transaction(self.config.accrualBeanFileName, tx_id)


def split_money_decimal(total_amount, n):
    total = Decimal(str(total_amount)).quantize(Decimal('0.01'))
    total_cents = int(total * 100)
    base_cents, remainder_cents = divmod(total_cents, n)
    parts = [Decimal(base_cents) / 100 for _ in range(n)]
    for i in range(remainder_cents):
        parts[i] += Decimal('0.01')
    return parts
