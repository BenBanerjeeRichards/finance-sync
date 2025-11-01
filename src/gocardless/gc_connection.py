from gocardless.gocardless import GcTranscation, GoCardlessClient
from model import GcStore, SantanderTransaction, SantanderTransactions, Config
from storage import ObjectNotFound, Store, SANTANDER_TX_FILE, GC_STORE_FILE
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from decimal import Decimal
import datetime
import processor
import santander

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

    def serve(self):
        server = ReqServer(('0.0.0.0', 8080), HttpHandler, self)
        logging.info("GC server running on port 8080")
        server.serve_forever()



class ReqServer(HTTPServer):

    def __init__(self, server_address, handler, sync: GcConnection):
        super().__init__(server_address, handler)
        self.sync = sync

class HttpHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        params = parse_qs(parsed_url.query)
        if path.endswith("/start-requisition"):
            link = self.server.sync.start_requisition()
            self.send_response(302)
            self.send_header("Location", link)
            self.end_headers()
        elif path.endswith("/complete-requisition"):
            ref = params.get("ref", [None])[0]
            status_html = "OK"

            if params.get("error"):
                logging.warning("Failed requisition %s - %s", ref,  params)
                status_html = """
                    Failed to compelete requisition: {} -  {}
                """.format(params.get("error", [""])[0], params.get("details", [""])[0])

            if ref is None:
                logging.warning("No ref provided to complete-requisition")
                status_html = "Error: No requisition specified"
            elif not params.get("error"):
                self.server.sync.complete_requisition(ref)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write("""
                <html>
                    <head>
                        <title>GoCardless Complete Requisition</title>
                    </head>
                    <body>
                        <h1> GoCardless Complete Authentication </h1>
                        {}
                    </body>
                </html>
                """.format(status_html).encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write("""
                <html>
                    <head>
                        <title>Not Found</title>
                    </head>
                    <body>
                        <h1>Not Found</h1>
                    </body>
                </html>
                """.encode())
