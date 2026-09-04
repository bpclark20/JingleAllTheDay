from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jingleserver.jsrv import cache, config, web


class CacheIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_dir = tempfile.TemporaryDirectory()
        self._original_cache_dir = config.CACHE_DIR
        config.CACHE_DIR = Path(self._temporary_dir.name)

    def tearDown(self) -> None:
        config.CACHE_DIR = self._original_cache_dir
        self._temporary_dir.cleanup()

    def test_cache_id_is_opaque_and_matches_live_cache_filename(self) -> None:
        path = r"C:\Jingles\Opening Theme.mp3"

        cache_id = cache.cache_id_for_path(path)

        self.assertTrue(cache.is_valid_cache_id(cache_id))
        self.assertEqual(cache.live_cache_relpath(path), f"live_{cache_id}.mp3")
        self.assertEqual(cache.preview_cache_relpath(cache_id), f"preview_{cache_id}.m4a")

    def test_manifest_lookup_resolves_only_known_cache_ids(self) -> None:
        path = r"C:\Jingles\Opening Theme.mp3"
        item = {"name": "Opening Theme", "path": path, "size_bytes": 1234}
        cache.write_manifest([item])

        self.assertEqual(cache.find_manifest_item(cache.cache_id_for_path(path)), item)
        self.assertIsNone(cache.find_manifest_item("not-a-cache-id"))
        self.assertIsNone(cache.find_manifest_item("0" * 64))

    def test_offline_library_filtering_matches_search_scope_and_paging(self) -> None:
        items = [
            {"name": "Morning News", "path": r"C:\Jingles\News.wav", "categories": ["News"]},
            {"name": "Crowd Cheer", "path": r"D:\Effects\Cheer.wav", "categories": ["Sports"]},
            {"name": "Evening News", "path": r"C:\Jingles\Evening.wav", "categories": ["News"]},
        ]

        filtered, total = web._offline_library_items(items, "news", "name", "", "any", 1, 1)

        self.assertEqual(total, 2)
        self.assertEqual(filtered, [items[2]])
        filtered, total = web._offline_library_items(items, "sports", "tag", "", "any", 0, 0)
        self.assertEqual(total, 1)
        self.assertEqual(filtered, [items[1]])
        filtered, total = web._offline_library_items(items, "effects", "path", "", "any", 0, 0)
        self.assertEqual(total, 1)
        self.assertEqual(filtered, [items[1]])


if __name__ == "__main__":
    unittest.main()