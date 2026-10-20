"""Vector store adapters (ADR-0002, ADR-0003).

Both adapters build their tenant boundary and filter from a `RetrievalScope`
themselves; neither accepts a namespace or a filter from the caller.
"""
