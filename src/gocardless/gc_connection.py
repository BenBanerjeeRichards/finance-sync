from gocardless.gocardless import GoCardlessClient
from model import GcStore, Config
from storage import Store, GC_STORE_FILE
import logging

class MissingTransactionId(Exception):
    pass


class GcConnection:

    """
    Responsible for managing requisitions to GC
    """

    def __init__(self, client: GoCardlessClient, store: Store, config: Config):
        self.client = client
        self.store = store
        self.config = config

    def start_requisition(self) -> str:
        self.client.get_new_tokens()
        return self.client.create_requisition().link

    def complete_requisition(self, ref: str):
        self.client.get_new_tokens()
        req = self.client.get_requisition(ref)
        if len(req.accounts) == 0:
            logging.error("No account exist on completed req %s", id)
            return
        if len(req.accounts) > 1:
            logging.error("Multiple accounts exist on req %s: %s. Picking first", id, req.accounts)
        account_id = req.accounts[0]
        logging.info("Completed requisition %s", req)
        self.store.write(GC_STORE_FILE, GcStore(requisition_id=ref, account_id=account_id))

        # Now delete any existing requisitions - probably not needed but good to clean up
        prev_reqs = [r for r in self.client.get_requisitions() if r.institution_id == req.institution_id and r.id != req.id]
        logging.info("Found %s existing requisitions to delete", len(prev_reqs))
        for prev_req in prev_reqs:
            self.client.delete_requisition(prev_req.id)
