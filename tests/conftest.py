import pytest
from app.db.base import Base
from app.db.session import engine


@pytest.fixture(autouse=True)
def database_schema() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
