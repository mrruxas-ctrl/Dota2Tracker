from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db import TrackerDatabase, extract_digits, normalize_id
from ocr_service import find_exact_digit_matches, find_id_matches, similarity_percent
from steam_profile import parse_steam_level, steam_id64_from_text, steam_profile_url_from_id


class NormalizeIdTests(unittest.TestCase):
    def test_normalize_id_trims_and_collapses_whitespace(self) -> None:
        self.assertEqual(normalize_id("  ABC\n  123\tXYZ  "), "abc 123 xyz")

    def test_extract_digits_returns_longest_number(self) -> None:
        self.assertEqual(extract_digits("Steam ID: 192856396 score 12"), "192856396")


class TrackerDatabaseTests(unittest.TestCase):
    def test_upsert_prevents_duplicate_rows_and_counts_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackerDatabase(Path(tmp) / "tracker.db")
            try:
                first = db.upsert_player("Steam ID: 192856396")
                second = db.upsert_player("id 192856396 copied again")
                players = db.get_players()

                self.assertEqual(first["status"], "inserted")
                self.assertEqual(second["status"], "updated")
                self.assertEqual(len(players), 1)
                self.assertEqual(players[0]["seen_count"], 2)
                self.assertEqual(players[0]["steam_id_text"], "192856396")
            finally:
                db.close()

    def test_ignored_id_is_not_inserted_as_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackerDatabase(Path(tmp) / "tracker.db")
            try:
                ignored = db.add_ignored("self id 192856396", "me")
                result = db.upsert_player(" profile 192856396 ")

                self.assertEqual(ignored["status"], "inserted")
                self.assertEqual(result["status"], "ignored")
                self.assertEqual(db.get_players(), [])
            finally:
                db.close()

    def test_player_level_can_be_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = TrackerDatabase(Path(tmp) / "tracker.db")
            try:
                db.upsert_player("192856396")
                db.update_player_level("192856396", 4)
                players = db.get_players()

                self.assertEqual(players[0]["steam_level"], 4)
                self.assertTrue(players[0]["level_checked_at"])
            finally:
                db.close()


class OcrMatchTests(unittest.TestCase):
    def test_exact_containment_scores_100(self) -> None:
        self.assertEqual(similarity_percent("76561198000000000", "profile 76561198000000000 open"), 100)

    def test_find_id_matches_respects_threshold(self) -> None:
        players = [
            {"id_key": "76561198000000000", "steam_id_text": "76561198000000000", "note": ""},
            {"id_key": "11111111111111111", "steam_id_text": "11111111111111111", "note": ""},
        ]
        matches = find_id_matches("enemy profile 76561198000000000", players, 80)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["steam_id_text"], "76561198000000000")
        self.assertEqual(matches[0]["score"], 100)

    def test_find_exact_digit_matches_requires_full_digit_id(self) -> None:
        players = [
            {"id_key": "192856396", "steam_id_text": "192856396", "note": ""},
            {"id_key": "123456789", "steam_id_text": "123456789", "note": ""},
        ]
        matches = find_exact_digit_matches("profile id 192856396 score 12", players)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["steam_id_text"], "192856396")
        self.assertEqual(matches[0]["score"], 100)


class SteamProfileTests(unittest.TestCase):
    def test_short_account_id_converts_to_steam_id64(self) -> None:
        self.assertEqual(steam_id64_from_text("192856396"), "76561198153122124")

    def test_steam_id64_is_used_directly(self) -> None:
        self.assertEqual(steam_profile_url_from_id("76561198153122124"), "https://steamcommunity.com/profiles/76561198153122124")

    def test_steam2_id_converts_to_profile_url(self) -> None:
        self.assertEqual(steam_id64_from_text("STEAM_0:0:96428198"), "76561198153122124")

    def test_parse_steam_level_from_profile_html(self) -> None:
        html = '<span class="friendPlayerLevelNum">4</span>'
        self.assertEqual(parse_steam_level(html), 4)


if __name__ == "__main__":
    unittest.main()
