class ProductionAcceptanceStandardV60:
    REQUIRED=(
      "local_preflight","health","hardened_runtime","pep_default_deny",
      "verification","rc_generation","rollback","audit","external_model","github_repository"
    )
    def evaluate(self,results):
        missing=[x for x in self.REQUIRED if x not in results]
        failed=[x for x in self.REQUIRED if x in results and not results[x]]
        return {"passed":not missing and not failed,"missing":missing,"failed":failed}
