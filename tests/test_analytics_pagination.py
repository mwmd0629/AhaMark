import pytest
from app.api.domain import ApiProblem
from app.api.results import page_result


def test_analytics_page_result_is_stable_and_reports_total() -> None:
    rows = [{"student_number": f"{index:04d}"} for index in range(25)]
    result = page_result(rows, page=2, page_size=10)
    assert result["total"] == 25
    assert result["pages"] == 3
    assert [row["student_number"] for row in result["items"]] == [
        f"{index:04d}" for index in range(10, 20)
    ]


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), (1, 101)])
def test_analytics_page_result_rejects_invalid_bounds(page: int, page_size: int) -> None:
    with pytest.raises(ApiProblem) as error:
        page_result([], page, page_size)
    assert error.value.code == "ANALYTICS_DRILLDOWN_INVALID"
