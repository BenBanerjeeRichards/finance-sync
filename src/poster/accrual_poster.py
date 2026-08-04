import dependencies
from poster.base_poster import BasePoster
from ledger.ledger_service import LedgerService
from main import Session
from model import AccrualConfig, SimpleLedgerTransaction
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


class AccrualsPoster(BasePoster):
    """
    The aim of this is to help splitting out things that are billed in arrears for multiple periods (months)
    E.g. a quarterly bill only received at the end of the Q
    If treated on a cash basis, it creates a spike in Expenses at the end of the Q which makes it harder to understand
    the actual expenses

    There are two parts to this.
        1. When a (e.g.) quarterly bill is paid (settlement), create the liabilities in the prior 3 months. Only one
           month below shown for example indicating the liability (also 2 prior also created) and the settlement

        01/03/2026  Factor Liability
            Expense:Factor              £80
                Liability:Factor                £80

        30/03/2026  Factor Settlement
            Liability:Factor            £240
                Asset:Monzo                     £240

        2. When settlement has not yet occurred but a bill is expected, create provisional liabiltes based on the
           past settlement amounts. This means we continue to book expenses prior to settlement. After settlement,
           these are replaced with the actual liabilities
    """

    def __init__(self, accrual_config: AccrualConfig):
        self.config = dependencies.get_config()
        self.ledger_service = dependencies.get_ledger_service()
        self.accrual_config = accrual_config

    def run(self):
        with Session.begin() as session:
            self.process_accrual(session, self.accrual_config)

    def process_accrual(self, session: "Session", rule: AccrualConfig):
        logging.info("Calculating accruals for rule %s", rule.metadata_key)
        settlements = LedgerService.find_all_by_metadata_by_date_desc(session, rule.metadata_key, VALUE_SETTLEMENT)
        provisional_liabilities = LedgerService.find_all_by_metadata_by_date_desc(session, rule.metadata_key,
                                                                                  VALUE_PROVISIONAL_LIABILITY)
        if not settlements:
            # If we have not yet had any actual transactions, we have nothing to base the liabilities on
            logging.warning("No settlements found for rule %s, can't compute any liabilities", rule.metadata_key)
            return

        settlement_transactions: list[SimpleLedgerTransaction] = []

        for settlement in settlements:
            liability_amounts = split_money_decimal(abs(settlement.entries[0].amount), rule.settlement_months)
            liability_months = [(settlement.transaction_datetime.date() - relativedelta(months=n + 1)).replace(day=1)
                                for n in
                                range(rule.settlement_months)]

            for i, liability_date in enumerate(liability_months):
                liability_key = f"{settlement.key}-{liability_date.isoformat()}"
                tx = SimpleLedgerTransaction(external_id=liability_key, tx_date=liability_date,
                                             credit_account_id=rule.liability_account, debit_account_id=rule.expense_account,
                                             payee=settlement.payee or "",
                                             description=f"{rule.name} - incurred liability",
                                             flagged=False,
                                             ledger_metadata={
                                                 rule.metadata_key: VALUE_LIABILITY,
                                                 "source": "accrual"
                                             }, source="accrual", amount=abs(liability_amounts[i]),
                                             metadata={})
                settlement_transactions.append(tx)

        self.ledger_service.create_or_update_simple_transactions(settlement_transactions)

        settlements = LedgerService.find_all_by_metadata_by_date_desc(session, rule.metadata_key, VALUE_SETTLEMENT)
        if not settlements:
            logging.warning("(provisional) no settlements found for rule %s", rule.name)
            return
        most_recent_settlement = settlements[0]
        provisional_transactions: list[SimpleLedgerTransaction] = []

        diff = relativedelta(datetime.date.today(), most_recent_settlement.transaction_datetime.date())
        months_since_last_settlement = (diff.years * 12) + diff.months + 1  # 1 to include current month

        if months_since_last_settlement <= 0:
            logging.info("skipping provisional liabilities for %s due to no months since last settlement %s", rule.name,
                         most_recent_settlement)
            return

        # we include the last settlement month in case settlement occurs often doesn't include that month
        # this would lead to a gap
        provisional_liability_months = [
            (most_recent_settlement.transaction_datetime.date() + relativedelta(months=n)).replace(day=1) for n in
            range(months_since_last_settlement)]

        # Take 10% more than the highest from recent transactions to be slightly pessimistic
        max_recent = max([abs(s.entries[0].amount) for s in settlements[:4]])
        estimated_amount = Decimal(str(max_recent)) * Decimal('1.10')
        estimated_liability = split_money_decimal(estimated_amount, rule.settlement_months)[0]
        logging.info("liability %s: computing provisional liabilities for month %s (amount %s)", rule.name,
                     provisional_liability_months, estimated_liability)
        for i, liability_date in enumerate(provisional_liability_months):
            liability_key = f"provisional-{most_recent_settlement.key}-{liability_date.isoformat()}"
            tx = SimpleLedgerTransaction(external_id=liability_key, tx_date=liability_date,
                                         credit_account_id=rule.liability_account, debit_account_id=rule.expense_account,
                                         payee=most_recent_settlement.payee or "",
                                         description=f"{rule.name} - provisional liability",
                                         flagged=False,
                                         ledger_metadata={
                                             rule.metadata_key: VALUE_PROVISIONAL_LIABILITY,
                                             "source": "accrual"
                                         }, source="accrual", amount=abs(estimated_liability))
            provisional_transactions.append(tx)

        # Find any provisional liabilities that exist in journal but not created here
        # These should be deleted to ensure this process is idempotent
        new_transaction_ids = [tx.external_id for tx in provisional_transactions]
        existing_provisional_ids = [pl.key for pl in provisional_liabilities]
        delete_tx_keys = set(existing_provisional_ids) - set(new_transaction_ids)
        if delete_tx_keys:
            logging.warning("%s: deleting transactions: %s", rule.name, delete_tx_keys)

        self.ledger_service.create_or_update_simple_transactions(provisional_transactions)
        self.ledger_service.delete_transactions_by_key(session, list(delete_tx_keys))

def split_money_decimal(total_amount, n):
    total = Decimal(str(total_amount)).quantize(Decimal('0.01'))
    total_cents = int(total * 100)
    base_cents, remainder_cents = divmod(total_cents, n)
    parts = [Decimal(base_cents) / 100 for _ in range(n)]
    for i in range(remainder_cents):
        parts[i] += Decimal('0.01')
    return parts
