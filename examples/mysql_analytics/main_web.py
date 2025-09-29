import pathlib

from utu.agents.orchestra_agent import OrchestraAgent
from utu.config import ConfigLoader
from utu.ui.webui_chatbot import WebUIChatbot


if __name__ == "__main__":
    # Load our prebuilt orchestra config
    config = ConfigLoader.load_agent_config("generated/mysql_data_analysis_agent")

    # Optional: prompt suggestion
    example_query_path = pathlib.Path(__file__).parent / "EXAMPLE_QUERY_CN.txt"
    example_query = example_query_path.read_text(encoding="utf-8") if example_query_path.exists() else ""

    agent = OrchestraAgent(config=config)
    ui = WebUIChatbot(agent, example_query=example_query)
    # Launch on default 127.0.0.1:8848 so your existing frontend ws url works
    ui.launch()

