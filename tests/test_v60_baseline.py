import unittest
from production_baseline.profile_v60 import ProductionProfileV60
from production_baseline.secrets_v60 import SecretBoundaryV60
from production_baseline.acceptance_v60 import ProductionAcceptanceStandardV60

class TestV60Baseline(unittest.TestCase):
    def test_profile_is_frozen(self):
        p=ProductionProfileV60()
        self.assertEqual(p.runtime_version,"6.0")
        self.assertEqual(p.side_effect_policy,"default-deny")
        self.assertEqual(p.required_actions,("run","verify","release","recover","status"))

    def test_secret_export_never_contains_values(self):
        safe=SecretBoundaryV60().export_safe()
        self.assertIn("configured",safe)
        self.assertIn("keys_present",safe)
        self.assertNotIn("values",safe)

    def test_acceptance_requires_external_integrations(self):
        s=ProductionAcceptanceStandardV60()
        base={k:True for k in s.REQUIRED}
        base["external_model"]=False
        base["github_repository"]=False
        result=s.evaluate(base)
        self.assertFalse(result["passed"])
        self.assertEqual(set(result["failed"]),{"external_model","github_repository"})

if __name__=="__main__": unittest.main()
