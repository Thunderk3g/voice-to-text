"""Unit tests: dominant-language bucketing for prompt overlays."""

from __future__ import annotations

from app.prompts.extraction_lang import detect_dominant_language, system_prompt_for

URDU_SAMPLE = (
    "میں اپنی پالیسی کے بارے میں بات کرنا چاہتا ہوں۔ "
    "پریمیم کی رقم کتنی ہے؟ کلیم کا طریقہ کیا ہے؟ "
    "مجھے دستاویزات کہاں جمع کرانی ہوں گی؟ "
) * 5

DEVANAGARI_SAMPLE = (
    "मैं अपनी पॉलिसी के बारे में बात करना चाहता हूं। प्रीमियम कितना है? "
) * 5


def test_arabic_script_detected_as_ur() -> None:
    assert detect_dominant_language(URDU_SAMPLE) == "ur"


def test_devanagari_still_hi() -> None:
    assert detect_dominant_language(DEVANAGARI_SAMPLE) == "hi"


def test_english_still_en() -> None:
    text = "I want to ask about my policy premium and the claim process." * 5
    assert detect_dominant_language(text) == "en"


def test_ur_overlay_present_and_prepended() -> None:
    prompt = system_prompt_for("ur")
    assert "Urdu" in prompt
    assert "english_gloss" in prompt
