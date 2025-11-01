from pydantic import BaseModel
from decimal import Decimal
from src.beancount_sync.beancount_sync import BeancountTransaction
from model import Transaction as MonzoTransaction
from santander import SantanderTransaction

class TransactionEvent(BaseModel):
    external_id: str 
    credit_account: str 
    debit_account: str 
    amount: Decimal 
    currency: str = "GBP"
    payee: str 
    description: str 
    # metadata is the card specific data 
    metadata: dict | None = None


    @staticmethod
    def from_ledger_transaction(tx: BeancountTransaction) -> "TransactionEvent":
        return TransactionEvent(external_id=tx.external_id, credit_account=tx.credit_account, debit_account=tx.debit_account,
                                payee=tx.payee, description=tx.description, amount=tx.amount)

    @staticmethod
    def from_transaction(tx: BaseModel, beancount_tx: BeancountTransaction):
        result = TransactionEvent.from_ledger_transaction(beancount_tx)
        result.metadata = tx.model_dump()
        return result

    @staticmethod
    def from_monzo_transaction(monzo_tx: MonzoTransaction, tx: BeancountTransaction) -> "TransactionEvent":
        result = TransactionEvent.from_ledger_transaction(tx)
        result.metadata = monzo_tx.model_dump()
        return result

    @staticmethod
    def from_santander_transaction(santander_tx: SantanderTransaction, tx: BeancountTransaction) -> "TransactionEvent":
        result = TransactionEvent.from_ledger_transaction(tx)
        result.metadata = santander_tx.model_dump()
        return result
