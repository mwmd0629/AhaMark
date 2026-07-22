from workers.tasks.demo import echo


def test_demo_task() -> None:
    assert echo.run("homework") == {"status": "processed", "value": "homework"}
