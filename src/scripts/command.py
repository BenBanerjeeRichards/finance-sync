import datetime
from dateutil.relativedelta import relativedelta
import time
from beancount_sync.beancount_util import *
from beancount.parser import printer


def retry(func):
    while True:
        try:
            return func()
        except Exception as e:
            print(f"Failed: {e} - try again")
            time.sleep(0.1)
            continue
        return


# Quickly create transactions for each month
def monthly_input():
    description = input("Description> ")
    credit_account = input("Credit account (money source)> ")
    debit_account = input("Debit account (money destination)> ")
    start_date_str = input("Start date (MM/YYYY)> ")
    num_months = input("Number of months> ")

    parts = start_date_str.split("/")
    assert len(parts) == 2
    current_date = datetime.date(year=int(parts[1]), month=int(parts[0]), day=1)
    txs = []
    for i in range(int(num_months)):
        amount = retry(lambda: Decimal(input(f"{current_date} Amount> ")))

        debit_amount = create_amount_from_decimal(amount)
        credit_amount = create_amount_from_decimal(amount * -1)

        debit_posting = new_posting(account=debit_account, units=debit_amount)
        credit_posting = new_posting(account=credit_account, units=credit_amount)
        tx = new_transaction(current_date, flag="*", postings=[credit_posting, debit_posting], payee=description)
        current_date = current_date + relativedelta(months=1)
        txs.append(tx)

    res = ""
    for t in txs:
        res += printer.format_entry(t) + "\n"
    print(res)

def main():
    monthly_input()

if __name__ == "__main__":
    main()
