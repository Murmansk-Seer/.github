import unittest
import yaml
from scripts.install_callers import caller


class Upgrade(unittest.TestCase):
    def test_preserves_config_refresh_and_schedule(self):
        workflow = {
            "on": {"schedule": [{"cron": "7 * * * *"}]},
            "jobs": {
                "sync": {"uses": "Murmansk-Seer/.github/.github/workflows/sync-identical-reusable.yml@" + "a" * 40,
                         "with": {"implementation_ref": "a" * 40}},
                "refresh": {"needs": "sync", "steps": [{"run": "echo preserved"}]},
            },
        }
        result = yaml.safe_load(caller("config-sources", "b" * 40, yaml.safe_dump(workflow), 55))
        self.assertEqual(result["jobs"]["refresh"], workflow["jobs"]["refresh"])
        self.assertEqual(result["on"], workflow["on"])
        self.assertEqual(result["jobs"]["sync"]["with"]["implementation_ref"], "b" * 40)
