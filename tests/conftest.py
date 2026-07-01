from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class FakeMessage:
    content: list
    stop_reason: str


class FakeStream:
    def __init__(self, chunks, final):
        self._chunks = chunks
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        yield from self._chunks

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
        chunks = [b.text for b in final.content if b.type == "text"]
        return FakeStream(chunks, final)


class FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)
