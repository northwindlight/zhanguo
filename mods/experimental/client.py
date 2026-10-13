"""Experimental monarch presentation. Review authority belongs to the session."""
from clients.llm import LLMClient, LLMState


class ParliamentClient(LLMClient):
    version='parliament/2'

    def __init__(self,cfg,*,record=None,services,backend=None):
        state=None
        if record is not None:
            if record.get('kind')!='llm' or record.get('version')!=self.version:
                raise ValueError('Unsupported parliament client state')
            state=LLMState.from_save(record['state'])
        super().__init__(cfg,state,backend=backend,ui=services['ui'])

    def _system(self,player):
        return super()._system(player)+self.ui.text('experimental.monarch')
