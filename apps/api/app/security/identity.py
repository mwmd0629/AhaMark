import re
import unicodedata

from pydantic import EmailStr, TypeAdapter


def normalize_email(value: str) -> str:
    normalized = value.lower().strip()
    if normalized.endswith(".synthetic.invalid") and "@" in normalized:
        local, domain = normalized.rsplit("@", 1)
        if local and domain.endswith(".synthetic.invalid"):
            return normalized
    if "@" in normalized:
        local, domain = normalized.rsplit("@", 1)
        if domain == "ahamark.local":
            validated = str(TypeAdapter(EmailStr).validate_python(f"{local}@example.com"))
            return f"{validated.rsplit('@', 1)[0]}@ahamark.local"
    return str(TypeAdapter(EmailStr).validate_python(normalized))


def normalize_recovery_email(value: str) -> str:
    """Normalize a user-supplied recovery address."""

    return normalize_email(value)


def normalize_login_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized or len(normalized) > 64:
        raise ValueError("学生学号长度必须为 1–64 个字符")
    if "@" in normalized or re.search(r"\s", normalized):
        raise ValueError("学生登录学号不能包含 @ 或空白字符")
    return normalized
