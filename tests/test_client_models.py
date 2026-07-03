from luban import client
from conftest import FakeClient


def test_list_models_returns_ids():
    fc = FakeClient([], model_ids=["claude-sonnet-5", "claude-fable-5"])
    assert client.list_models(fc) == ["claude-sonnet-5", "claude-fable-5"]


def test_list_models_none_on_exception():
    fc = FakeClient([], model_ids=None)  # models.list() raises
    assert client.list_models(fc) is None


def test_list_models_none_on_empty():
    fc = FakeClient([], model_ids=[])
    assert client.list_models(fc) is None


def test_list_models_unwraps_paginated_data():
    class Page:
        def __init__(self, data):
            self.data = data

    class M:
        def __init__(self, id):
            self.id = id

    class C:
        class models:
            @staticmethod
            def list():
                return Page([M("a"), M("b")])

    assert client.list_models(C) == ["a", "b"]
