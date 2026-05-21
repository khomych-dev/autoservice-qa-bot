"""Tests for pure helper functions in app.main."""
from __future__ import annotations

import pytest

from app.main import _parse_filename


class TestParseFilename:
    @pytest.mark.parametrize(
        "filename, expected_call_type",
        [
            # English keywords
            ("incoming_380671234567_20260521.mp3", "Вхідний"),
            ("outgoing_380671234567_20260521.mp3", "Вихідний"),
            ("INCOMING_call.wav", "Вхідний"),
            ("OUTGOING_call.wav", "Вихідний"),
            # Prefix variants
            ("in_380671234567.ogg", "Вхідний"),
            ("out_380671234567.ogg", "Вихідний"),
            # Embedded variants
            ("call_in_380671234567.mp3", "Вхідний"),
            ("call_out_380671234567.mp3", "Вихідний"),
            # Ukrainian keywords
            ("вхідний_380671234567.mp3", "Вхідний"),
            ("вихідний_380671234567.mp3", "Вихідний"),
            # No keyword → empty string
            ("380671234567_20260521.mp3", ""),
            ("recording_001.mp3", ""),
        ],
    )
    def test_call_type_detection(
        self, filename: str, expected_call_type: str
    ) -> None:
        call_type, _ = _parse_filename(filename)
        assert call_type == expected_call_type

    @pytest.mark.parametrize(
        "filename, expected_phone",
        [
            # 12-digit international format
            ("incoming_380671234567_20260521.mp3", "380671234567"),
            ("380501112233.wav", "380501112233"),
            # 10-digit local format
            ("0671234567_call.mp3", "0671234567"),
            # 12-digit takes priority over 10-digit
            ("380671234567_prefix_0671234567.mp3", "380671234567"),
            # No number → empty string
            ("recording_001.mp3", ""),
            ("nophones.wav", ""),
        ],
    )
    def test_phone_number_extraction(
        self, filename: str, expected_phone: str
    ) -> None:
        _, phone_number = _parse_filename(filename)
        assert phone_number == expected_phone

    def test_returns_both_values_simultaneously(self) -> None:
        call_type, phone_number = _parse_filename(
            "incoming_380671234567_20260521_143022.mp3"
        )
        assert call_type == "Вхідний"
        assert phone_number == "380671234567"

    def test_no_match_returns_empty_strings(self) -> None:
        call_type, phone_number = _parse_filename("audio_record_001.mp3")
        assert call_type == ""
        assert phone_number == ""
