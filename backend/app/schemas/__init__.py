"""Pydantic request/response schemas.

These define the `data` payload only. The surrounding envelope
(`success`/`meta`/`error`/`request_id`) is added by `core.response`, so no
schema here should ever include those keys.
"""
