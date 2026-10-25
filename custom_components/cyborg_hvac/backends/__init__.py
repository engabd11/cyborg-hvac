"""Brand backends.

Each brand lives in its own package. Modules in a brand package must not import
Home Assistant, except ``adapter.py``, which is the brand's only bridge to it
(authentication flow helpers and backend construction from a config entry).
"""
