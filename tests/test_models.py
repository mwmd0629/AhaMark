from app.models import AuthEmailChallenge, User


def test_user_model_constraints() -> None:
    table = User.__table__
    assert table.c.email.unique
    assert table.c.email.nullable is True
    assert table.c.login_name.unique
    assert table.c.login_name.index
    assert table.c.login_name.nullable is True
    assert table.c.email_verified_at.nullable is True
    assert table.c.email_verified_at.type.timezone is True
    assert table.c.password_hash.nullable is False
    assert table.c.created_at.type.timezone is True


def test_auth_email_challenge_model_constraints() -> None:
    table = AuthEmailChallenge.__table__
    assert table.c.user_id.nullable is False
    assert table.c.purpose.type.length == 32
    assert table.c.email_snapshot.type.length == 320
    assert table.c.code_hash.type.length == 64
    assert table.c.expires_at.type.timezone is True
    assert table.c.consumed_at.type.timezone is True
    assert table.c.attempts.nullable is False
    assert table.c.created_at.nullable is False
    user_fk = next(iter(table.c.user_id.foreign_keys))
    assert user_fk.target_fullname == "users.id"
    assert user_fk.ondelete == "CASCADE"
