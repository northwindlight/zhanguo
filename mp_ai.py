"""LLM client entry. Game rules and rule-client selection are not owned here."""
from clients.llm import LLMClient,LLMState,assert_paired


def execute(player,tool,args):
    return player.submit(tool,args)


def run_openai_turn(player,cfg,*,state=None,backend=None,**kwargs):
    client=LLMClient(cfg,state,backend)
    return client.play(player,**kwargs)

__all__=['LLMClient','LLMState','execute','run_openai_turn','assert_paired']
