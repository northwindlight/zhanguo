"""Compose model-facing text and image snapshots without owning client memory."""
import json
from copy import deepcopy
from .i18n import Translator
from .maps import snapshot,owner_color
from .views import player_map


class LLMUI:
    def __init__(self,cfg):self.cfg=cfg;self.text=Translator.from_config(cfg)

    def system(self):
        return self.text('llm.system')+'\n'+str(self.cfg.get('system_prompt') or '')+'\n'+str(self.cfg.get('extra_summary') or '')

    def actions(self,schemas):
        # Translate only annotations; names, argument keys, enums and IDs are API.
        schemas=deepcopy(schemas)
        def annotations(node,path):
            if isinstance(node,dict):
                for key,value in list(node.items()):
                    if key in ('description','title') and isinstance(value,str):
                        node[key]=self.text.resolve(path+'.'+key,value)
                    elif isinstance(value,(dict,list)):annotations(value,path+'.'+key)
            elif isinstance(node,list):
                for index,value in enumerate(node):annotations(value,path+'.'+str(index))
        for schema in schemas:
            fn=schema['function'];annotations(fn,'tools.'+fn['name'])
        return schemas

    def state(self,observation,events):
        tail=json.dumps({'state':observation,'events':events},ensure_ascii=False,sort_keys=True)
        if observation['period']<self.cfg.get('joined_at',0)+20 and self.cfg.get('extra_prompt'):
            tail+='\n'+self.text('llm.background',text=self.cfg['extra_prompt'])
        legend,image=self.map_context(observation)
        return tail+legend,image

    def map_context(self,observation):
        legend='';image=None
        if self.cfg.get('vision',False) and observation['facts'].get('view'):
            facts=snapshot(observation)
            owners=sorted({t['owner'] for t in facts['tiles'] if t['owner']})
            legend+='\n'+self.text('map.legend',owners=', '.join(f'{owner}={owner_color(owner)}' for owner in owners))
            image={'facts':facts,'max_px':self.cfg.get('map_max_px',960),'estimated_tokens':self.cfg.get('map_estimated_tokens',2048),
                   'fallback':self.text('map.fallback')+'\n'+player_map(observation,self.text)}
        return legend,image

    def context_plan(self,p):
        k=lambda n:f'{int(n or 0)/1000:.1f}k' if int(n or 0)<1000000 else f'{int(n)/1000000:.2f}M'
        t=self.text
        head=t('context.fixed',turns=p.fixed_turns) if p.mode=='fixed' else t('context.budget',window=k(p.window),budget=k(p.budget),ratio=(p.budget/p.window*100 if p.window else 0))
        if p.roll=='period':head+=t('context.period',turns=p.period) if p.period else t('context.auto',turns=p.effective_period or '?')
        total=p.total_tokens or 1;hit=t('context.hit',ratio=p.stable_tokens/total*100) if p.stable_tokens else ''
        return t('context.detail',head=head,turns=p.replay_turns,replay=k(p.replay_tokens),archive=k(p.archive_tokens),state=k(p.tail_tokens),system=k(p.sys_tokens),total=k(total),hit=hit)
