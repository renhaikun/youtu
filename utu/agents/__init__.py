from ..config import AgentConfig
from .llm_agent import LLMAgent
from .orchestra_agent import OrchestraAgent
from .simple_agent import SimpleAgent
from .workforce_agent import WorkforceAgent


def get_agent(config: AgentConfig) -> SimpleAgent | OrchestraAgent | WorkforceAgent:
    if config.type == "simple":
        return SimpleAgent(config=config)
    elif config.type == "orchestra":
        return OrchestraAgent(config=config)
    elif config.type == "workforce":
        return WorkforceAgent(config=config)
    else:
        raise ValueError(f"Unknown agent type: {config.type}")


__all__ = [
    "SimpleAgent",
    "OrchestraAgent",
    "LLMAgent",
    "WorkforceAgent",
    "get_agent",
]
