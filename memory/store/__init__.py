from memory.store.base import BaseStore
from memory.store.file_store import FileStore

# Future: swap to SqliteStore here without touching any caller.
def get_store() -> BaseStore:
    return FileStore()
