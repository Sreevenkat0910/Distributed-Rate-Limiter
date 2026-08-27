import pytest
from fastapi.testclient import TestClient

from app.limiter import store
from app.main import app


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """The rate limiter's dict store is a module-level global shared by
    every app instance, so it must be reset between tests to keep them
    isolated from each other."""
    store._requests.clear()
    yield
    store._requests.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
