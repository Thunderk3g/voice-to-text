from app.utils.crux_id import crux_call_id_from_name


def test_extracts_eight_digit_id():
    assert crux_call_id_from_name("25689211.mp3") == "25689211"

def test_extracts_from_path_like_name():
    assert crux_call_id_from_name("38199637.MP3") == "38199637"

def test_non_numeric_stem_is_none():
    assert crux_call_id_from_name("upload.mp3") is None

def test_none_is_none():
    assert crux_call_id_from_name(None) is None

def test_empty_is_none():
    assert crux_call_id_from_name("") is None

def test_mixed_stem_is_none():
    assert crux_call_id_from_name("call_25689211_v2.mp3") is None
