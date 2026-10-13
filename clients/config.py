"""LLM client owns context and model transport configuration."""
from engine.config import Field, Schema
from clients.context import CTX_KEYS

TRANSPORT_FIELDS={key:Field((str,)) for key in (
    'base_url','api_key','model','reasoning_effort','anthropic_version')}
TRANSPORT_FIELDS.update({
    'provider':Field((str,),choices=('openai','anthropic')),
    'thinking':Field((str,),choices=('enabled','disabled','adaptive')),
    'temperature':Field((int,float),minimum=0,maximum=2,nullable=True),
    'max_tokens':Field((int,),minimum=1), 'api_retries':Field((int,),minimum=0),
    'api_retry_wait':Field((int,float),minimum=0),
    'anthropic_auth':Field((str,),choices=('bearer','x-api-key','both')),
    'anthropic_beta':Field((str,list)), 'anthropic_cache_control':Field((bool,dict)),
    'anthropic_thinking_budget':Field((int,),minimum=1),
    'anthropic_tail_max':Field((int,),minimum=0),
})
for key in ('api_timeout','api_ttft_timeout','api_chunk_timeout'):
    TRANSPORT_FIELDS[key]=Field((int,float),minimum=0.001)
CONTEXT_FIELDS={key:Field((int,),minimum=0) for key in CTX_KEYS}
for key in ('ctx_window','ctx_compact_tokens','ctx_period'):CONTEXT_FIELDS[key]=Field((int,),minimum=1)
for key in ('ctx_fill','ctx_slide_keep','ctx_archive_fill','ctx_slice_keep'):
    CONTEXT_FIELDS[key]=Field((int,float),minimum=0,maximum=1)
CONTEXT_FIELDS['small_ctx']=Field((bool,))
CONTEXT_FIELDS['ctx_compact']=Field((str,),choices=('llm','off'))
CONTEXT_FIELDS['ctx_old_reasoning']=Field((str,),choices=('full','strip'))
CONTEXT_FIELDS['ctx_roll']=Field((str,),choices=('slide','period','periodic','none'))
LLM_SCHEMA=Schema({**TRANSPORT_FIELDS,**CONTEXT_FIELDS,'max_steps':Field((int,),minimum=1)})


def normalize(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    return LLM_SCHEMA.normalize(cfg,defaults=defaults,overrides=overrides,path=path)

normalize.fields=LLM_SCHEMA.fields
