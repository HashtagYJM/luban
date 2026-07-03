from dataclasses import dataclass, field
from types import SimpleNamespace


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    thinking: str = ""
    signature: str = ""
    data: str = ""


@dataclass
class FakeMessage:
    content: list
    stop_reason: str


def _delta_event(dtype, **kw):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=dtype, **kw))


class FakeStream:
    def __init__(self, final):
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        # Mirror the Anthropic streaming schema: text/thinking arrive as
        # content_block_delta events.
        for b in self._final.content:
            if b.type == "text":
                yield _delta_event("text_delta", text=b.text)
            elif b.type == "thinking":
                yield _delta_event("thinking_delta", thinking=b.thinking)

    @property
    def text_stream(self):
        yield from (b.text for b in self._final.content if b.type == "text")

    def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        final = self._scripted.pop(0)
        return FakeStream(final)


class _FakeModels:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        if self._ids is None:
            raise RuntimeError("models.list unsupported")
        return [SimpleNamespace(id=i) for i in self._ids]


class FakeClient:
    def __init__(self, scripted, model_ids=None):
        self.messages = _FakeMessages(scripted)
        self.models = _FakeModels(model_ids)
