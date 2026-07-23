import pytest
from app.db.base import Base
from app.db.session import engine
from sqlalchemy.orm import close_all_sessions


@pytest.fixture(autouse=True)
def database_schema() -> None:
    close_all_sessions()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    close_all_sessions()
    Base.metadata.drop_all(engine)
