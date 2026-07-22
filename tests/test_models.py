from app.models import User


def test_user_model_constraints() -> None:
    table = User.__table__
    assert table.c.email.unique
    assert table.c.password_hash.nullable is False
    assert table.c.created_at.type.timezone is True
