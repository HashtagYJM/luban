"""Wire-protocol adapters. One module per non-Anthropic backend.

luban is Anthropic wire-shaped end to end — block content, `stop_reason` vocabulary,
`cache_control`, `input_schema`, `input_tokens`/`cache_*` usage names. An adapter's job is
to make another provider answer to that surface, so nothing above `client.py` learns there
is more than one backend.
"""
