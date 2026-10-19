"""LLM client owns context and model transport configuration."""
from engine.config import Field, Schema, ConfigError
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


# ── 模型接入的必填项 ──────────────────────────────────────────────────
# 配置需求要能落地成一句话：模型客户端至少要能说清「连到哪、用什么密钥、用哪个模型」。
# 少填就在开局前说清楚，别等第一次请求时抛一个 KeyError。
MODEL_FIELDS=('base_url','api_key','model')
MODEL_FIELD_HELP={
    'base_url':'OpenAI 兼容端点地址（provider 缺省即 openai，也可写 anthropic）',
    'api_key':'该端点的密钥',
    'model':'模型名（如 deepseek-v4-flash）',
}
# 模板里的占位文案不算填好了。
PLACEHOLDER_MARKS=('粘贴','填写','你的','your api key','填入','TODO','xxx')


def _placeholder(value):
    text=str(value or '').strip()
    if not text:return True
    lowered=text.lower()
    return any(mark.lower() in lowered for mark in PLACEHOLDER_MARKS)


def model_issues(cfg,*,path='config'):
    """列出配置里所有模型接入的缺口：[(位置, 缺哪些字段)]。

    只看 client 为 llm 的国家；规则客户端不需要密钥。
    """
    issues=[]
    nations=cfg.get('nations') or []
    if not isinstance(nations,list):return issues
    for index,nation in enumerate(nations):
        if not isinstance(nation,dict):continue
        if str(nation.get('client') or '').strip().lower()!='llm':continue
        missing=[key for key in MODEL_FIELDS if _placeholder(nation.get(key))]
        if missing:issues.append((f'{path}.nations[{index}]',nation.get('name') or '',missing))
    return issues


def require_model_fields(cfg,*,path='config',clients=()):
    """缺模型接入字段就抛 ConfigError，消息说清缺什么、怎么补。"""
    issues=model_issues(cfg,path=path)
    if not issues:return
    lines=[]
    for location,name,missing in issues:
        lines.append(f'{location}' + (f'（{name}）' if name else '') + '：缺少 ' + '、'.join(missing))
        for key in missing:
            lines.append(f'  · {key}：{MODEL_FIELD_HELP[key]}')
    if 'rule' in clients:
        lines.append('  不想接模型就把这个国家的 "client" 改成 "rule"（用内置规则 AI）。')
    elif clients:
        lines.append('  不想接模型就把这个国家的 "client" 改成 '
                     + '、'.join(sorted(name for name in clients if name != 'llm'))
                     + '（本发行版声明的其它客户端）。')
    lines.append('  模板见 mp_config.example.json；密钥只写进 mp_config.json（该文件不入库）。')
    raise ConfigError('\n'.join(lines))
