"""Copy this file to `client_local.py` and edit build_client().

`client_local.py` is gitignored; it is the ONLY place a company-specific
import should appear. build_client() must return an object exposing:
  - .messages.create(model=..., max_tokens=..., system=..., messages=..., tools=...)
  - .messages.stream(...)  (optional; run with --no-stream if unsupported)
"""


def build_client():
    # Replace the two lines below with your organization's client:
    #   from your_internal_pkg import YourClient
    #   return YourClient(env="...", timeout=..., num_retries=...).client()
    raise NotImplementedError("Edit build_client() in client_local.py")
