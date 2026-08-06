import datetime
import uuid
from decimal import Decimal

import dependencies
from ledger.dto import EntryDto, AccountDto, AccountType
from ledger.ledger_service import LedgerService
from ledger.repo import TransactionFilters
from main import Session
from model import MortgageConfig
from poster.base_poster import BasePoster
import logging
from dateutil.relativedelta import relativedelta

# Start of the ledger, may come after initial mortgage payment
LEDGER_BEGIN = datetime.date(year=2024, month=4, day=1)


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
    """

    def __init__(self, mortgage_config: MortgageConfig):
        self.mortgage_config = mortgage_config

    def run(self) -> None:
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

        with Session.begin() as session:
            payment_history = LedgerService.find_all_by_metadata_by_date_desc(session, "mortgage", "pending")

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
                primary_amount = primary_payment.absolute_amount()
                other_payments = [p for p in month_payments if p.id != primary_payment.id]
                if primary_amount >= Decimal("1.01") * est_monthly_payment:
                    primary_overpayment_amount = primary_amount - est_monthly_payment
                else:
                    primary_overpayment_amount = Decimal("0")

                remaining_principal = MortgagePoster._outstanding_mortgage_principal_on_date(
                    self.mortgage_config.mortgageLiabilityAccount, month)
                if not remaining_principal:
                    logging.warning("Failed to find remaining principal for month %s", month)
                    continue
                interest_amount = remaining_principal * monthly_interest_rate
                if interest_amount >= primary_amount:
                    logging.warning("Interest %s exceeds monthly payment %s", interest_amount, primary_amount)
                    continue

                interest_entry = EntryDto(id=uuid.uuid4(), transaction_id=primary_payment.id, amount=interest_amount,
                                          local_amount=interest_amount, local_currency="GBP",
                                          account=AccountDto(id=self.mortgage_config.mortgageInterestAccount))
                principal_entry = EntryDto(id=uuid.uuid4(), transaction_id=primary_payment.id,
                                           amount=primary_amount - interest_amount,
                                           local_amount=primary_amount - interest_amount, local_currency="GBP",
                                           account=AccountDto(id=self.mortgage_config.mortgageLiabilityAccount))
                existing_credits = [e for e in primary_payment.entries if e.amount < 0]
                primary_payment.entries = [interest_entry, principal_entry, *existing_credits]
                primary_payment.tx_metadata["mortgage"] = "computed"
                if primary_overpayment_amount > Decimal("0"):
                    primary_payment.tx_metadata["mortgage_overpayment"] = f"{primary_overpayment_amount:.2f}"
                ledger_service.create_or_update_transactions([primary_payment])

                for dedicated_overpayment in other_payments:
                    overpayment = dedicated_overpayment.absolute_amount()
                    principal_entry = EntryDto(id=uuid.uuid4(), transaction_id=primary_payment.id,
                                               amount=overpayment,
                                               local_amount=overpayment, local_currency="GBP",
                                               account=AccountDto(id=self.mortgage_config.mortgageLiabilityAccount))
                    existing_credits = [e for e in dedicated_overpayment.entries if e.amount < 0]
                    dedicated_overpayment.tx_metadata["mortgage_overpayment"] = f"{overpayment:.2f}"
                    dedicated_overpayment.entries = [principal_entry, *existing_credits]
                    ledger_service.create_or_update_transactions([dedicated_overpayment])

    @staticmethod
    def _outstanding_mortgage_principal_on_date(liability_account: uuid.UUID, dt: datetime.date) -> Decimal | None:
        bal_filters = TransactionFilters(account_id=liability_account,
                                         created_lt=datetime.datetime.combine(dt, datetime.time.min,
                                                                     tzinfo=datetime.timezone.utc))

        balances = LedgerService.get_balance(bal_filters, account_types=[AccountType.LIABILITY.value]).balances
        filtered = [b for b in balances if b.account_id == liability_account]
        if not filtered:
            return None
        return abs(filtered[0].amount)

    @staticmethod
    def _monthly_payment(interest_percent: Decimal, principal: Decimal, term_months: int) -> Decimal:
        monthly_interest_rate = interest_percent / Decimal("1200")
        factor = (1 + monthly_interest_rate) ** term_months
        return principal * (monthly_interest_rate * factor) / (factor - 1)
