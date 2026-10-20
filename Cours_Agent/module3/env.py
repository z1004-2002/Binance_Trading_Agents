from dataclasses import dataclass

@dataclass
class EnvProperties:
    observable: str # "full" or "partial"
    determinism: str # "deterministic" or "stochastic"
    episodic: str # "episodic" or "sequential"
    static: str # "static" or "dynamic"
    discrete: str # "discrete" or "continuous"
    single_agent: str # "single-agent" or "multi-agent"
    
    def recommend_architecture(self):
        if self.observable == "partial" and self.static == "dynamic":
            return "Model-based ou hybride (etat interne requis)"
        if self.determinism == "stochastic":
            return "Utility'based (arbitrage risque/rendement)"
        return "simple reflex peut suffire"
    
crypto_env = EnvProperties(
    observable="partial",
    determinism="stochastic",
    episodic="sequential",
    static="dynamic",
    discrete="continuous",
    single_agent="single-agent"
)

print(crypto_env.recommend_architecture())