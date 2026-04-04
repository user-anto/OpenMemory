import os
import tempfile


# Ensure module-level store initialization (CLI/MCP) uses a writable path during tests.
os.environ["MEMORY_STORE_PATH"] = tempfile.mkdtemp(prefix="llm-memory-test-", dir="/tmp")
