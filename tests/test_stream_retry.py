"""Mid-stream disconnects: 'peer closed connection without sending complete message
body (incomplete chunked read)'. The SDK's max_retries cannot cover this — it retries
failures that happen while ISSUING a request; once a 200 is streaming and bytes have
been consumed, a severed body is handed to the caller. Only a fresh request can retry."""
import pytest

from luban import client as client_mod


class RemoteProtocolError(Exception):
    """Same class NAME httpx raises — matched by name so luban imports no httpx."""


class BadRequestError(Exception):
    status_code = 400


class RateLimitError(Exception):
    status_code = 429


FIELD_ERROR = RemoteProtocolError(
    "peer closed connection without sending complete message body "
    "(incomplete chunked read)"
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(client_mod, "_EXTRAS_SUPPORTED", None)


# ---------------- classification ----------------

def test_the_field_error_is_transient():
    assert client_mod.is_transient(FIELD_ERROR)


def test_a_4xx_is_not_transient():
    """A malformed request fails identically forever — retrying is pure waste."""
    assert not client_mod.is_transient(BadRequestError("bad tool schema"))


def test_429_is_transient():
    assert client_mod.is_transient(RateLimitError("slow down"))


def test_a_plain_bug_is_not_transient():
    assert not client_mod.is_transient(KeyError("thinking"))


# ---------------- retry behaviour ----------------

class FakeStream:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self._text


class FlakyClient:
    """Drops the stream `fails` times, then succeeds."""

    def __init__(self, fails, exc=FIELD_ERROR):
        self.fails = fails
        self.exc = exc
        self.attempts = 0
        self.messages = self

    def stream(self, **kw):
        self.attempts += 1
        if self.attempts <= self.fails:
            raise self.exc
        return FakeStream("recovered")

    def create(self, **kw):
        self.attempts += 1
        if self.attempts <= self.fails:
            raise self.exc
        return "recovered"


def _stream(client, **kw):
    return client_mod.stream_turn(
        client, model="m", max_tokens=10, system="s", messages=[], tools=[],
        on_text=lambda t: None, **kw)


def test_a_dropped_stream_is_retried_and_recovers():
    c = FlakyClient(fails=2)
    assert _stream(c) == "recovered"
    assert c.attempts == 3  # two drops, then through


def test_retries_are_bounded_then_the_error_surfaces():
    c = FlakyClient(fails=99)
    with pytest.raises(RemoteProtocolError):
        _stream(c)
    assert c.attempts == client_mod.STREAM_RETRIES + 1


def test_a_real_error_is_not_retried():
    c = FlakyClient(fails=99, exc=BadRequestError("bad schema"))
    client_mod._EXTRAS_SUPPORTED = True  # past the probe, so errors are real
    with pytest.raises(BadRequestError):
        _stream(c)
    assert c.attempts == 1  # no pointless retry loop


def test_the_user_is_told_each_retry():
    seen = []
    c = FlakyClient(fails=1)
    _stream(c, on_retry=lambda exc, n, total, delay: seen.append((n, total, delay)))
    assert seen == [(1, client_mod.STREAM_RETRIES, client_mod._RETRY_BACKOFF[0])]


def test_non_streaming_turns_retry_too():
    """/compact, /reflect and subagents don't stream — same gateway, same cut."""
    c = FlakyClient(fails=2)
    out = client_mod.create_turn(c, model="m", max_tokens=10, system="s",
                                 messages=[], tools=[])
    assert out == "recovered" and c.attempts == 3


# ---------------- the probe landmine ----------------

def test_a_dropped_connection_never_disables_thinking():
    """_EXTRAS_SUPPORTED starts as None (unprobed). A blip on turn one used to be read
    as 'this backend rejects thinking/effort' and silently disabled them PROCESS-WIDE."""
    c = FlakyClient(fails=99)
    with pytest.raises(RemoteProtocolError):
        _stream(c, thinking=True, effort="xhigh")
    assert client_mod._EXTRAS_SUPPORTED is None  # still unprobed — not condemned to False


def test_a_genuine_rejection_still_disables_extras():
    """The graceful-degrade path must survive: a backend that really rejects the
    thinking/effort params still falls back to a plain request, exactly as before."""
    class Rejects:
        def __init__(self):
            self.calls = []
            self.messages = self

        def stream(self, **kw):
            self.calls.append(kw)
            if "thinking" in kw:
                raise TypeError("unexpected keyword argument 'thinking'")
            return FakeStream("plain")

    c = Rejects()
    assert _stream(c, thinking=True, effort="high") == "plain"
    assert client_mod._EXTRAS_SUPPORTED is False
    assert len(c.calls) == 2  # probed with extras, then retried without
