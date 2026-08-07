# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_manager


class UpdateManagerTests(unittest.TestCase):
    def test_migration_only_adds_missing_values(self):
        with tempfile.TemporaryDirectory() as work:
            config = Path(work) / "config.json"
            state = Path(work) / "state.json"
            config.write_text(json.dumps({"agent_default": False, "custom": {"mine": 1}}), encoding="utf-8")
            manifest = {"release": "1.2.0", "patches": [{
                "id": "safe-defaults",
                "config_defaults": {"agent_default": True, "custom": {"mine": 9, "new": 2}},
            }]}
            with patch.object(update_manager, "CONFIG", config), patch.object(update_manager, "STATE", state):
                result = update_manager.apply_migrations(manifest)
                saved = json.loads(config.read_text(encoding="utf-8"))
                self.assertFalse(saved["agent_default"])
                self.assertEqual(saved["custom"]["mine"], 1)
                self.assertEqual(saved["custom"]["new"], 2)
                self.assertEqual(result["applied"], ["safe-defaults"])

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as work:
            config = Path(work) / "config.json"
            state = Path(work) / "state.json"
            config.write_text("{}", encoding="utf-8")
            manifest = {"release": "1", "patches": [{"id": "once", "config_defaults": {"new": 1}}]}
            with patch.object(update_manager, "CONFIG", config), patch.object(update_manager, "STATE", state):
                first = update_manager.apply_migrations(manifest)
                second = update_manager.apply_migrations(manifest)
                self.assertEqual(first["applied"], ["once"])
                self.assertEqual(second["applied"], [])
                self.assertFalse(second["changed"])

    def test_manifest_has_unique_patch_ids(self):
        manifest = update_manager.local_manifest()
        ids = [row["id"] for row in manifest.get("patches", [])]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(manifest.get("release"))

    def test_automatic_check_refreshes_at_most_once_per_window(self):
        with tempfile.TemporaryDirectory() as work:
            state = Path(work) / "state.json"
            with patch.object(update_manager, "STATE", state), patch.object(
                update_manager, "update_status", return_value={"ok": True, "available": False}
            ) as status:
                update_manager.automatic_update_status(max_age=3600)
                update_manager.automatic_update_status(max_age=3600)
                self.assertEqual(status.call_args_list[0].kwargs, {"refresh": True})
                self.assertEqual(status.call_args_list[1].kwargs, {"refresh": False})

    def test_backup_copies_private_config_and_custom_workflows(self):
        with tempfile.TemporaryDirectory() as work:
            repo = Path(work) / "repo"
            backups = repo / "backups"
            (repo / "workflows").mkdir(parents=True)
            (repo / "config.json").write_text('{"telegram_token":"secret"}', encoding="utf-8")
            (repo / "workflows" / "mine.json").write_text("{}", encoding="utf-8")
            with patch.object(update_manager, "HERE", repo), patch.object(update_manager, "BACKUPS", backups):
                target = update_manager.backup_user_data()
                self.assertTrue((target / "config.json").is_file())
                self.assertTrue((target / "workflows" / "mine.json").is_file())
                self.assertTrue((target / "backup.json").is_file())


if __name__ == "__main__":
    unittest.main()
