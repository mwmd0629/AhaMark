import builtins
import math
from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.recognition.pipeline import (
    RAPIDOCR_MAX_BLOCKS,
    RAPIDOCR_MAX_INPUT_BYTES,
    PageArtifact,
    RapidOcrProvider,
    RecognitionError,
    UnavailableProvider,
    parse_rapidocr_output,
    provider_from_settings,
)
from pydantic import ValidationError


def _page() -> PageArtifact:
    return PageArtifact(b"synthetic image bytes", 100, 100, "image/png")


def _output(
    *, boxes: object = None, texts: object = None, scores: object = None
) -> SimpleNamespace:
    return SimpleNamespace(
        boxes=[[[10, 10], [30, 10], [30, 20], [10, 20]]] if boxes is None else boxes,
        txts=["text"] if texts is None else texts,
        scores=[0.9] if scores is None else scores,
    )


def test_rapidocr_flags_default_closed_and_reject_true_in_every_environment() -> None:
    settings = Settings(_env_file=None)
    assert settings.recognition_rapidocr_runtime_enabled is False
    assert settings.recognition_rapidocr_model_download_allowed is False

    for app_env in ("development", "test", "production"):
        for field in (
            "recognition_rapidocr_runtime_enabled",
            "recognition_rapidocr_model_download_allowed",
        ):
            with pytest.raises(ValidationError, match="RapidOCR configuration rejected"):
                Settings(_env_file=None, app_env=app_env, **{field: True})


def test_factory_is_stably_unavailable_without_importing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "rapidocr" or name.startswith("onnxruntime"):
            raise AssertionError("runtime import is forbidden")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    settings = Settings(_env_file=None, recognition_provider="rapidocr")
    provider = provider_from_settings(settings)

    assert isinstance(provider, UnavailableProvider)
    assert provider.available() == (
        False,
        "RapidOCR 运行时未授权，且禁止在服务进程中下载模型",
    )
    assert RapidOcrProvider().available()[0] is False


def test_injected_engine_is_lazy_cached_and_output_is_sorted() -> None:
    calls = 0
    raw = _output(
        boxes=[
            [[60, 60], [90, 60], [90, 80], [60, 80]],
            [[10, 10], [30, 10], [30, 20], [10, 20]],
        ],
        texts=["second", "first"],
        scores=[0.7, 0.95],
    )

    def factory() -> object:
        nonlocal calls
        calls += 1
        return lambda _content: raw

    provider = RapidOcrProvider(engine_factory=factory, version="test-injected-v1")

    assert provider.available()[0] is True
    assert [block.text for block in provider.recognize(_page())] == ["first", "second"]
    assert [block.text for block in provider.recognize(_page())] == ["first", "second"]
    assert calls == 1


@pytest.mark.parametrize(
    "page",
    [
        PageArtifact(b"", 10, 10, "image/png"),
        PageArtifact(b"x", 0, 10, "image/png"),
        PageArtifact(b"x", True, 10, "image/png"),
        PageArtifact(b"x", 10, 10, "application/pdf"),
        PageArtifact(b"x" * (RAPIDOCR_MAX_INPUT_BYTES + 1), 10, 10, "image/png"),
    ],
)
def test_invalid_input_is_rejected_before_engine(page: PageArtifact) -> None:
    called = False

    def factory() -> object:
        nonlocal called
        called = True
        return lambda _content: _output()

    with pytest.raises(RecognitionError) as error:
        RapidOcrProvider(engine_factory=factory, version="test").recognize(page)
    assert error.value.code == "OCR_PROVIDER_INPUT_INVALID"
    assert called is False


@pytest.mark.parametrize(
    "raw",
    [
        SimpleNamespace(boxes=[], txts=[], scores=None),
        _output(boxes=[], texts=["text"], scores=[0.9]),
        _output(boxes=[[[0, 0], [1, 0], [1, 1]]]),
        _output(boxes=[[[0, 0], [101, 0], [101, 1], [0, 1]]]),
        _output(boxes=[[[0, 0], [1, 0], [1, 0], [0, 0]]]),
        _output(boxes=[[[0, 0], [math.nan, 0], [1, 1], [0, 1]]]),
        _output(texts=[1]),
        _output(texts=[""]),
        _output(texts=[" \t\r\n"]),
        _output(scores=[True]),
        _output(scores=[1.1]),
        _output(scores=[math.inf]),
    ],
)
def test_invalid_raw_output_has_stable_error(raw: object) -> None:
    with pytest.raises(RecognitionError) as error:
        parse_rapidocr_output(raw, _page())
    assert error.value.code == "OCR_PROVIDER_OUTPUT_INVALID"


@pytest.mark.parametrize(
    "text",
    [
        "abc\u061cdef",
        "abc\u200edef",
        "abc\u202edef",
        "abc\u2066def\u2069",
        "abc\ud800def",
        "abc\ufdd0def",
        "abc\ufffedef",
        "abc\U0010ffffdef",
    ],
)
def test_unsafe_unicode_text_has_stable_output_error(text: str) -> None:
    with pytest.raises(RecognitionError) as error:
        parse_rapidocr_output(_output(texts=[text]), _page())
    assert error.value.code == "OCR_PROVIDER_OUTPUT_INVALID"


def test_normal_chinese_english_and_math_unicode_remain_allowed() -> None:
    text = "中文 English α∑x²≤3"

    blocks = parse_rapidocr_output(_output(texts=[text]), _page())

    assert [block.text for block in blocks] == [text]


@pytest.mark.parametrize("failure_stage", ["factory", "inference"])
def test_engine_failures_have_stable_non_leaking_error(failure_stage: str) -> None:
    private_detail = "private engine path C:\\secret\\model.onnx"

    def factory() -> object:
        if failure_stage == "factory":
            raise RuntimeError(private_detail)

        def engine(_content: bytes) -> object:
            raise RuntimeError(private_detail)

        return engine

    with pytest.raises(RecognitionError) as error:
        RapidOcrProvider(engine_factory=factory, version="test").recognize(_page())

    assert error.value.code == "OCR_INFERENCE_FAILED"
    assert str(error.value) == "本地文字 OCR 推理失败"
    assert private_detail not in str(error.value)


def test_output_limits_and_empty_result() -> None:
    assert parse_rapidocr_output(SimpleNamespace(boxes=None, txts=None, scores=None), _page()) == []
    too_many = [[[0, 0], [1, 0], [1, 1], [0, 1]] for _ in range(RAPIDOCR_MAX_BLOCKS + 1)]
    with pytest.raises(RecognitionError) as error:
        parse_rapidocr_output(
            _output(boxes=too_many, texts=["x"] * len(too_many), scores=[0.5] * len(too_many)),
            _page(),
        )
    assert error.value.code == "OCR_PROVIDER_OUTPUT_INVALID"
