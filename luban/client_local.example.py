"""Copy this file to `client_local.py` and edit build_client().

`client_local.py` is gitignored; it is the ONLY place a company-specific
import should appear. build_client() must return an object exposing:
  - .messages.create(model=..., max_tokens=..., system=..., messages=..., tools=...)
  - .messages.stream(...)  (optional; run with --no-stream if unsupported)

OPTIONAL SECOND PROVIDER
Define build_openai_client() as well and luban routes by model id: `gpt-*` (and `o1`/
`o3`/`o4`) go to OpenAI, everything else to build_client(). /model then switches provider
mid-session with no restart. Return the raw OpenAI client — luban wraps it; the wire
translation lives in luban/providers/openai.py, not here.

This file's job is credentials and endpoint wiring. Nothing else belongs in it: it is
untested, unreviewed and never distributed, so protocol logic placed here would be
reinvented by every colleague and run in nobody's test suite.
"""


def build_client():
    # Replace the two lines below with your organization's client:
    #   from your_internal_pkg import YourClient
    #   return YourClient(env="...", timeout=..., num_retries=...).client()
    raise NotImplementedError("Edit build_client() in client_local.py")


# def build_openai_client():
#     from openai import OpenAI
#     return OpenAI(api_key=..., base_url=...)
