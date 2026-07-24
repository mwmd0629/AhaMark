import os
import time

from app.cli.recovery_v7_guard import RecoveryGuardError, require_recovery_environment

ALLOWED_CHECKPOINTS = {"recognition-running", "report-before-storage"}


def recovery_fault_checkpoint(name: str) -> None:
    requested = {
        value.strip()
        for value in os.environ.get("RECOVERY_V7_FAULT_CHECKPOINT", "").split(",")
        if value.strip()
    }
    if not requested or name not in requested:
        return
    if not requested <= ALLOWED_CHECKPOINTS:
        raise RecoveryGuardError("unknown recovery fault checkpoint")
    if os.environ.get("APP_ENV", "").lower() != "test":
        raise RecoveryGuardError("recovery fault checkpoints require APP_ENV=test")
    if os.environ.get("RECOVERY_V7_ENABLED", "").lower() != "true":
        raise RecoveryGuardError("recovery fault checkpoints require RECOVERY_V7_ENABLED=true")
    require_recovery_environment()
    try:
        delay = float(os.environ.get("RECOVERY_V7_FAULT_DELAY_SECONDS", "0"))
    except ValueError as exc:
        raise RecoveryGuardError("fault checkpoint delay must be numeric") from exc
    if not 1 <= delay <= 60:
        raise RecoveryGuardError("fault checkpoint delay must be between 1 and 60 seconds")
    time.sleep(delay)
