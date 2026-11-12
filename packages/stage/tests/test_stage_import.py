import stage


def test_stage_imports() -> None:
    assert stage.__doc__


def test_stage_sees_core() -> None:
    import core

    assert core.__doc__
