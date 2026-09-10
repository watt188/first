from dataclasses import dataclass,asdict
@dataclass(frozen=True)
class ProductionProfileV60:
    name:str="production"
    runtime_version:str="6.0"
    side_effect_policy:str="default-deny"
    required_actions:tuple=("run","verify","release","recover","status")
    require_external_model:bool=True
    require_github_repository:bool=True
    require_audit:bool=True
    def as_dict(self): return asdict(self)
