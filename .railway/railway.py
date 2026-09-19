from railway_sdk import define_railway, project, service

# This repository manages only its own resources in the environment. Other
# repositories export their own partial name.
# See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
PARTIAL = "delve-meme-agent"

@define_railway
def main(ctx=None):
    delve_meme_agent = service(
        "delve-meme-agent",
        start="python -m src.execution.live_entry_process_launcher",
    )
    return project("delve-meme-agent", resources=[delve_meme_agent])
