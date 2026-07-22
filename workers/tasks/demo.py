from workers.celery_app import celery_app


@celery_app.task(name="ahamark.demo.echo")
def echo(value: str) -> dict[str, str]:
    return {"status": "processed", "value": value}
