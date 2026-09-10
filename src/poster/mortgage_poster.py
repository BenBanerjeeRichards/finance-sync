import datetime
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

import dependencies
from ledger.dto import EntryDto, AccountDto, AccountType, TransactionDto
from ledger.ledger_service import LedgerService
from ledger.repo import TransactionFilters
from model import MortgageConfig
from poster.base_poster import BasePoster
import logging
from dateutil.relativedelta import relativedelta

# Start of the ledger, may come after initial mortgage payment
LEDGER_BEGIN = datetime.date(year=2024, month=4, day=1)
# kind of an awkward of doing this... but we need to  track which mortgage items are not posted by prior poster
# so we just leave them alone
SANTANDER_BEGIN = datetime.datetime(year=2025, month=2, day=1, tzinfo=datetime.timezone.utc)


class MortgagePoster(BasePoster):
    """
    The bank posters will create mortgage entries that post the full amount to an expense account
    This poster edits those to separate out the interest and principal so the principal parts instead debits the
    property liability
    Also supports overpayments both as scheduled into the main payment and additional one-off payments

    E.g. suppose a £200k mortgage, 5 year fixed, 35 year term at 3.39% (£813.88 monthly payment) with £200 overpayment

        Expense:Mortgage            £1013.88
           Asset:Monzo                          £1013.88

    Let's say based on existing principal, £500 of that payment is interest, the remainder is principal (part scheduled,
    part overpayment). The correct posting is

        Expense:Mortgage            £500
        Liability:House             £513.88
            Asset:Monzo                        £1013.88

    This then goes a step further and splits this payment into dedicated committed and overpayment entries
    So that we can account for them separatly in Spending views

      Primary mortgage payment #committed
        Expense:Mortgage            £500
        Liability:House             £313.88
            Asset:Monzo                        £813.88

      Mortgage overpayment #overpayment
        Liability:House             £200
            Asset:Monzo                        £500
    """

    def __init__(self, mortgage_config: MortgageConfig):
        self.mortgage_config = mortgage_config

    def run(self, session: Session) -> None:
        monthly_interest_rate = self.mortgage_config.interestPercent / Decimal("1200")
        # Estimated as banks calculate daily so this can be very slightly different (within 1%)
        est_monthly_payment = MortgagePoster._monthly_payment(self.mortgage_config.interestPercent,
                                                              self.mortgage_config.initialPrincipal,
                                                              self.mortgage_config.termMonths)

        logging.info("Running mortgage poster on rate=%s monthly_payment=%s", self.mortgage_config.interestPercent,
                     est_monthly_payment)

        start_date = self.mortgage_config.startDate.replace(day=1)
        end_date = min(start_date + relativedelta(months=self.mortgage_config.fixedRateMonths), datetime.date.today())
        ledger_service = dependencies.get_ledger_service()

        current = max(LEDGER_BEGIN, start_date)
        dates: list[datetime.date] = []
        while current <= end_date:
            dates.append(current)
            current += relativedelta(months=1)

        # First delete all computed items so we can start with a clean slate
        existing_computed = LedgerService.find_all_by_metadata_by_date_desc(session, "mortgage", "computed")
        # leave alone the computed ones on manually created payments otherwise we loose them
        to_delete_ids = [t.id for t in existing_computed if t.transaction_datetime >= SANTANDER_BEGIN]
        if len(to_delete_ids):
            logging.info("deleting %s existing computed mortgage transactions", len(to_delete_ids))
        LedgerService.delete_transactions(session, to_delete_ids)

        payment_history = LedgerService.find_all_by_metadata_by_date_desc(session, "mortgage", "pending")
        logging.info("processing mortgage transaction for period %s to %s", dates[0], dates[-1])
        for month in dates:
            month_payments = [tx for tx in payment_history if
                              tx.transaction_datetime.year == month.year and
                              tx.transaction_datetime.month == month.month]

            primary_payments = [p for p in month_payments if
                                p.absolute_amount() >= Decimal(
                                    "0.99") * est_monthly_payment]

            if not primary_payments:
                logging.info("No primary mortgage payment (%s) found for period %s", est_monthly_payment,
                             month)
                continue
            # Primary payment = main mortgage payment that is part principal, part interest
            # Part of the primary payment could be overpayment
            # All other payments are overpayments
            primary_payment = primary_payments[0]
            primary_credit_account = [e for e in primary_payment.entries if e.amount < Decimal("0")][0].account.id
            primary_amount = primary_payment.absolute_amount()
            other_payments = [p for p in month_payments if p.id != primary_payment.id]
            # 1.01 to adjust for that fact that bank's computation of monthly payment differs slightly
            if primary_amount >= Decimal("1.01") * est_monthly_payment:
                primary_overpayment_amount = primary_amount - est_monthly_payment
            else:
                primary_overpayment_amount = Decimal("0")

            remaining_principal = MortgagePoster._outstanding_mortgage_principal_on_date(
                session, self.mortgage_config.mortgageLiabilityAccount, month)
            if not remaining_principal:
                logging.warning("Failed to find remaining principal for month %s", month)
                continue
            interest_amount = remaining_principal * monthly_interest_rate
            if interest_amount >= primary_amount:
                logging.warning("Interest %s exceeds monthly payment %s", interest_amount, primary_amount)
                continue
            primary_principal = primary_amount - interest_amount - primary_overpayment_amount

            primary = self._create_mortgage_transaction(credit_account_id=primary_credit_account,
                                                        dt=primary_payment.transaction_datetime,
                                                        external_id=f"mortgage_primary_{primary_payment.key}",
                                                        group_id=primary_payment.group_id,
                                                        payee=primary_payment.payee,
                                                        narration=primary_payment.narration,
                                                        principal_amount=primary_principal,
                                                        interest_amount=interest_amount,
                                                        tags=["committed"])
            ledger_service.create_or_update_transactions(session, [primary])
            LedgerService.supersede_transaction(session, primary_payment.id, primary.group_id)
            if primary_overpayment_amount > Decimal("0"):
                p_overpayment = self._create_mortgage_transaction(credit_account_id=primary_credit_account,
                                                                  dt=primary_payment.transaction_datetime,
                                                                  external_id=f"mortgage_overpayment_{primary_payment.key}",
                                                                  group_id=primary_payment.group_id,
                                                                  payee=primary_payment.payee,
                                                                  narration=primary_payment.narration,
                                                                  principal_amount=primary_overpayment_amount,
                                                                  interest_amount=Decimal("0"),
                                                                  tags=["overpayment"])
                ledger_service.create_or_update_transactions(session, [p_overpayment])
                session.flush()

            for dedicated_overpayment in other_payments:
                acc_credit_id = [e for e in dedicated_overpayment.entries if e.amount < Decimal("0")][0].account.id
                overpayment = self._create_mortgage_transaction(credit_account_id=acc_credit_id,
                                                                dt=dedicated_overpayment.transaction_datetime,
                                                                external_id=f"mortgage_overpayment_{dedicated_overpayment.key}",
                                                                group_id=f"mortgage_overpayment_{dedicated_overpayment.key}",
                                                                payee=dedicated_overpayment.payee,
                                                                narration=dedicated_overpayment.narration,
                                                                principal_amount=dedicated_overpayment.absolute_amount(),
                                                                interest_amount=Decimal("0"),
                                                                tags=["overpayment"])

                ledger_service.create_or_update_transactions(session, [overpayment])
                session.flush()
                LedgerService.supersede_transaction(session, dedicated_overpayment.id, overpayment.group_id)

    def _create_mortgage_transaction(self, credit_account_id: uuid.UUID, dt: datetime.datetime, external_id: str,
                                     group_id: str, payee: str, narration: str,
                                     principal_amount: Decimal, interest_amount: Decimal,
                                     tags: list[str] | None = None) -> TransactionDto:
        tags = tags or []
        transaction = TransactionDto(id=uuid.uuid4(), transaction_datetime=dt, key=external_id, payee=payee,
                                     narration=narration, external_metadata={}, ledger_metadata={
                "mortgage": "computed",
                "source": "mortgage"
            }, entries=[], tags=tags, group_id=group_id)

        interest_entry = EntryDto(id=uuid.uuid4(), transaction_id=transaction.id, amount=interest_amount,
                                  local_amount=interest_amount, local_currency="GBP",
                                  account=AccountDto(
                                      id=self.mortgage_config.mortgageInterestAccount)) if interest_amount > 0 else None

        principal_entry = EntryDto(id=uuid.uuid4(), transaction_id=transaction.id,
                                   amount=principal_amount,
                                   local_amount=principal_amount, local_currency="GBP",
                                   account=AccountDto(id=self.mortgage_config.mortgageLiabilityAccount))

        credit_entry = EntryDto(id=uuid.uuid4(), transaction_id=transaction.id,
                                amount=abs(principal_amount + interest_amount) * -1,
                                local_amount=abs(principal_amount + interest_amount) * -1, local_currency="GBP",
                                account=AccountDto(id=credit_account_id))

        transaction.entries = [principal_entry, credit_entry]
        if interest_entry:
            transaction.entries.append(interest_entry)
        return transaction

    @staticmethod
    def _outstanding_mortgage_principal_on_date(session, liability_account: uuid.UUID, dt: datetime.date) -> Decimal | None:
        bal_filters = TransactionFilters(account_id=liability_account,
                                         created_lt=datetime.datetime.combine(dt, datetime.time.min,
                                                                              tzinfo=datetime.timezone.utc))

        balances = LedgerService.get_balance(session, bal_filters, account_types=[AccountType.LIABILITY.value]).balances
        filtered = [b for b in balances if b.account_id == liability_account]
        if not filtered:
            return None
        return abs(filtered[0].amount)

    @staticmethod
    def _monthly_payment(interest_percent: Decimal, principal: Decimal, term_months: int) -> Decimal:
        monthly_interest_rate = interest_percent / Decimal("1200")
        factor = (1 + monthly_interest_rate) ** term_months
        return principal * (monthly_interest_rate * factor) / (factor - 1)
