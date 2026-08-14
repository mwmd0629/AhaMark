import uuid
from types import SimpleNamespace
from typing import Any, cast

from app.assignment_generation.file_analysis import _role
from app.assignment_generation.textbook_sources import (
    _exercise_question_windows,
    _question_anchor_number,
    solution_overlap,
    text_signals,
)


def _block(text: str, y: float, order: int) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        text=text,
        y=y,
        x=0.1,
        display_order=order,
        block_type="text",
    )


def _page() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


def test_textbook_role_is_selectable_and_detected_conservatively() -> None:
    assert _role("数学分析讲义 第二册.pdf", "第 9 章 多变量函数的微分学") == (
        "textbook",
        0.82,
        "not_applicable",
        1.0,
    )


def test_solution_overlap_ranks_shared_formula_structure_without_claiming_equivalence() -> None:
    solution = "由 x^2+xy+y^2=7 隐式求导，得到 (2x+y)+(x+2y)y'=0。"
    source = "习题 9.3 1(1) x^2+xy+y^2=7，在 (2,1) 处求 y 对 x 的一阶和二阶导数。"
    unrelated = "设级数 sum a_n 收敛，讨论一致收敛性。"

    score, shared = solution_overlap(solution, source)
    unrelated_score, _ = solution_overlap(solution, unrelated)

    assert score > unrelated_score
    assert 0.08 <= score <= 0.79
    assert len(shared) >= 3
    assert text_signals(solution)


def test_solution_overlap_requires_multiple_shared_signals() -> None:
    assert solution_overlap("求导", "求导") == (0.0, [])


def test_textbook_index_keeps_only_numbered_questions_inside_exercises() -> None:
    page = _page()
    blocks = [
        _block("95", 0.04, 0),
        _block("习题 9.3", 0.16, 1),
        _block("1. 求函数的偏导数", 0.24, 2),
        _block("设函数连续且可微", 0.32, 3),
        _block("(1) 这是第一题的子项", 0.42, 4),
        _block("2x 是公式而不是题号", 0.50, 5),
        _block("2(3) 证明下列等式", 0.58, 6),
        _block("证明过程不进入下一题", 0.67, 7),
    ]

    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, page), cast(Any, blocks), None
    )

    assert page_exercise == "习题 9.3"
    assert next_exercise == "习题 9.3"
    assert [_question_anchor_number(window.anchor) for window in windows] == ["1", "2(3)"]
    assert all("习题 9.3" not in window.text for window in windows)


def test_textbook_question_extraction_skips_contents_body_and_examples() -> None:
    contents = [
        _block("目录", 0.10, 0),
        _block("习题 9.3 ........ 95", 0.30, 1),
        _block("1. 多变量函数", 0.40, 2),
    ]
    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, _page()), cast(Any, contents), None
    )
    assert windows == []
    assert page_exercise is None
    assert next_exercise is None

    body = [
        _block("例 9.3.4", 0.20, 0),
        _block("1. 这是例题推导中的编号", 0.35, 1),
    ]
    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, _page()), cast(Any, body), None
    )
    assert windows == []
    assert page_exercise is None
    assert next_exercise is None


def test_textbook_exercise_context_continues_then_stops_at_section_or_answers() -> None:
    continuation = [_block("3. 求极值", 0.20, 0), _block("函数条件", 0.30, 1)]
    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, _page()), cast(Any, continuation), "习题 9.3"
    )
    assert [_question_anchor_number(window.anchor) for window in windows] == ["3"]
    assert page_exercise == next_exercise == "习题 9.3"

    new_section = [_block("§9.4 空间曲线与曲面", 0.20, 0), _block("1. 正文编号", 0.30, 1)]
    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, _page()), cast(Any, new_section), next_exercise
    )
    assert windows == []
    assert page_exercise is None
    assert next_exercise is None

    answer_page = [
        _block("习题 9.4", 0.15, 0),
        _block("1. 求曲线方程", 0.25, 1),
        _block("答案", 0.50, 2),
        _block("2. 这是答案中的编号", 0.60, 3),
    ]
    windows, page_exercise, next_exercise = _exercise_question_windows(
        cast(Any, _page()), cast(Any, answer_page), None
    )
    assert [_question_anchor_number(window.anchor) for window in windows] == ["1"]
    assert page_exercise == "习题 9.4"
    assert next_exercise is None
