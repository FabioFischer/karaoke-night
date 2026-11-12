import studio


def test_studio_imports() -> None:
    assert studio.__doc__


def test_studio_sees_core() -> None:
    import core

    assert core.__doc__
