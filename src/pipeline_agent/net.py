"""Network setup shared by the CLI and scripts.

Corporate networks often inspect TLS with their own root CA. That CA lives in
the OS trust store, but `requests` (used by databricks-sdk) only trusts the
certifi bundle, so every call fails certificate verification and the SDK
retries until it times out. `truststore` makes Python's ssl module use the OS
store instead. Harmless on networks without inspection.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_done = False


def use_system_trust_store() -> None:
    global _done
    if _done:
        return
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        log.debug("truststore not installed; using certifi bundle")
    _done = True
