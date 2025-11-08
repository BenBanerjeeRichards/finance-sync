from model import MonzoStore, Transaction, GcStore
import minio
import json
from io import BytesIO
from pydantic import BaseModel
from typing import TypeVar, Type, Union
import logging

BUCKET = "transactions"
STORE_FILE = "monzo-store.json"
GC_STORE_FILE = "gc-store.json"
MONZO_TX_FILE = "transactions.json"
SANTANDER_TX_FILE = "santander-transactions.json"

class ObjectNotFound(Exception):
    pass

def load_file(minio_client: minio.Minio, bucket: str, name: str) -> dict:
    try:
        response = minio_client.get_object(bucket_name=bucket, object_name=name)
    except minio.error.S3Error as e:
        if e.code == "NoSuchKey":
            raise ObjectNotFound() from e
        logging.error("Failed to write file whilst reading bucket=%s key=%s", bucket, name)
        raise e

    js =  json.loads(response.read().decode("utf-8"))
    response.close()
    response.release_conn()
    return js


def write_file(minio_client: minio.Minio, bucket: str, name: str, contents: str) -> None:
    content_encoded = contents.encode()
    as_bytes = BytesIO(content_encoded)
    minio_client.put_object(bucket, name, data=as_bytes, length=len(content_encoded), content_type="text/plain")


def load_monzo_store(minio_client: minio.Minio) -> MonzoStore:
    return MonzoStore(**load_file(minio_client, BUCKET, STORE_FILE))


def write_monzo_store(minio_client: minio.Minio, store: MonzoStore):
    write_file(minio_client, BUCKET, STORE_FILE, store.json())


def load_gc_store(minio_client: minio.Minio) -> GcStore:
    return GcStore(**load_file(minio_client, BUCKET, GC_STORE_FILE))


def write_gc_store(minio_client: minio.Minio, store: GcStore):
    write_file(minio_client, BUCKET, GC_STORE_FILE, store.json())


T = TypeVar("T", bound=BaseModel)

class Store:

    def __init__(self, minio_client: minio.Minio, bucket: str = BUCKET):
        self.minio_client = minio_client
        self.bucket = bucket

    def load(self, name: str, t: Type[T]) -> T:
        js = load_file(self.minio_client, self.bucket, name)
        return t(**js)

    def load_list(self, name: str, t: Type[T]) -> list[T]:
        js = load_file(self.minio_client, self.bucket, name)
        return [t(**x) for x in js]


    def write(self, name: str, contents: Union[BaseModel, str, list[T]], bucket=None) -> None:
        bucket = self.bucket if not bucket else bucket
        s = None
        match contents:
            case str():
                s = contents
            case BaseModel():
                s = contents.model_dump_json()
            case list():
                s = json.dumps([x.model_dump_json() for x in contents])
        if not s:
            raise RuntimeError("Invalid s")
        write_file(self.minio_client, bucket, name, s)
