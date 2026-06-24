from app.utils.phone import normalize_mobile, clean_na


class TestNormalizeMobile:
    def test_plain_valid_ten_digit_unchanged(self):
        assert normalize_mobile("9876543210") == "9876543210"

    def test_strips_91_country_code(self):
        assert normalize_mobile("919876543210") == "9876543210"

    def test_strips_leading_zero(self):
        assert normalize_mobile("09876543210") == "9876543210"

    def test_strips_plus_91_and_punctuation(self):
        assert normalize_mobile("+91 98765-43210") == "9876543210"

    def test_handles_float_formatted_number(self):
        assert normalize_mobile("9876543210.0") == "9876543210"

    def test_handles_actual_float_input(self):
        assert normalize_mobile(9876543210.0) == "9876543210"

    def test_nine_digit_lost_leading_zero_is_invalid(self):
        assert normalize_mobile("987654321") is None

    def test_starts_with_one_is_invalid(self):
        assert normalize_mobile("1234567890") is None

    def test_starts_with_five_is_invalid(self):
        assert normalize_mobile("5876543210") is None

    def test_na_string_is_none(self):
        assert normalize_mobile("NA") is None

    def test_empty_string_is_none(self):
        assert normalize_mobile("") is None

    def test_none_input_is_none(self):
        assert normalize_mobile(None) is None

    def test_nan_input_is_none(self):
        assert normalize_mobile(float("nan")) is None

    def test_too_many_digits_after_strip_is_invalid(self):
        assert normalize_mobile("1234567890123") is None


class TestCleanNa:
    def test_literal_na_becomes_none(self):
        assert clean_na("NA") is None

    def test_lowercase_na_becomes_none(self):
        assert clean_na("na") is None

    def test_whitespace_only_becomes_none(self):
        assert clean_na("   ") is None

    def test_real_value_preserved_and_trimmed(self):
        assert clean_na("  Salaried  ") == "Salaried"

    def test_other_null_spellings_become_none(self):
        for spelling in ["None", "null", "NULL", "#N/A", "N/A", "nan", "-"]:
            assert clean_na(spelling) is None, spelling
