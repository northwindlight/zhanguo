"""Search a single client's action prose; never index hidden reasoning."""
import json
from copy import deepcopy

SCHEMA={'type':'function','function':{'name':'memory_search','description':'免费检索自己的历史行动、发言和回执正文，排除思考过程。关键词空格分隔，全命中优先，返回周期和邻近上下文；压缩上下文后仍可检索，不能读取其他国家记忆。',
 'parameters':{'type':'object','properties':{'query':{'type':'string','minLength':1},'limit':{'type':'integer','minimum':1,'maximum':8,'default':3}},'required':['query'],'additionalProperties':False}}}


HISTORY_LIMIT=240
PERIOD_TEXT_LIMIT=12000


def prose_receipt(receipt):
    """Fallback prose when no UI renderer exists; never copies observation JSON.

    Production always passes the rendered tool text (the exact prose the model
    saw) to :func:`action_text`. This fallback only runs for callers without a
    UI, so it keeps the message and status rather than a second data copy.
    """
    if not isinstance(receipt,dict):return str(receipt)
    parts=[]
    if receipt.get('message'):parts.append(str(receipt['message']))
    status=receipt.get('status')
    if status and status!='effective':parts.append('状态：'+str(status))
    data=receipt.get('data')
    if not parts and data is not None:
        if isinstance(data,dict):parts.append('回执数据：'+'、'.join(map(str,data)))
        elif isinstance(data,(list,tuple)):parts.append(f'回执数据 {len(data)} 项')
        else:parts.append(str(data))
    return '；'.join(parts)


def action_text(assistant,calls,receipts,contents=None):
    """One period of searchable prose: speech plus tool receipts as rendered.

    ``contents`` are the exact tool messages sent to the model. They are already
    the UI's Chinese prose, so the search index never stores a JSON copy of the
    observation. Without them, fall back to :func:`prose_receipt`.
    """
    parts=[assistant['content']] if isinstance(assistant.get('content'),str) and assistant['content'] else []
    for index,(call,receipt) in enumerate(zip(calls,receipts)):
        fn=call.get('function',{})
        name=fn.get('name')
        if name=='memory_search':continue
        rendered=contents[index] if contents and index<len(contents) else None
        text=rendered if isinstance(rendered,str) and rendered.strip() else prose_receipt(receipt)
        # 回执可能只说「信已发出，共 N 字」，承诺正文只存在于调用参数。
        # 参数也是玩家自己的行动记忆；写成散文，不能只存回执或抄观察 JSON。
        arguments=fn.get('arguments') or {}
        if isinstance(arguments,str):
            try:arguments=json.loads(arguments)
            except ValueError:arguments={}
        if isinstance(arguments,dict) and arguments:
            text='参数：'+_argument_prose(arguments)+'\n'+text
        parts.append(f'{name}：{text}' if text else str(name))
    return '\n'.join(parts)


def _argument_prose(value):
    if isinstance(value,dict):
        return '；'.join(str(key)+'＝'+_argument_prose(item) for key,item in value.items())
    if isinstance(value,(list,tuple)):
        return '、'.join(_argument_prose(item) for item in value)
    return str(value)


def _render_legacy(text,render):
    """Convert pre-0.4.1 JSON history lines to prose before searching.

    Old saves stored ``{"action":…,"arguments":…,"receipt":…}`` per tool call.
    Rendering only when a row is actually searched keeps the migration lazy and
    lets the selected UI own the wording.
    """
    parts=[];changed=False
    for line in text.splitlines():
        stripped=line.strip()
        if stripped.startswith('{'):
            try:record=json.loads(stripped)
            except ValueError:record=None
            if isinstance(record,dict) and 'action' in record and 'receipt' in record:
                parts.append(_render_record(record,render));changed=True;continue
        parts.append(line)
    return '\n'.join(parts) if changed else text


def _render_record(record,render):
    action=record.get('action')
    arguments=record.get('arguments') or {}
    if isinstance(arguments,str):
        try:arguments=json.loads(arguments)
        except ValueError:arguments={}
    if not isinstance(arguments,dict):arguments={}
    receipt=record.get('receipt') if isinstance(record.get('receipt'),dict) else {}
    data=receipt.get('data')
    if isinstance(data,str):
        try:data=json.loads(data)
        except ValueError:data=None
    rebuilt={**receipt,'ok':receipt.get('ok',True),'action':action,'data':data}
    prose=None
    try:prose=render(action,arguments,rebuilt)
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):prose=None
    if not isinstance(prose,str) or not prose.strip():prose=prose_receipt(rebuilt)
    if arguments:prose='参数：'+_argument_prose(arguments)+'\n'+prose
    return f'{action}：{prose}' if prose else str(action)


def record(history,period,text):
    if not text:return
    if history and history[-1]['period']==period:history[-1]['text']+='\n'+text
    else:history.append({'period':period,'text':text})
    if len(history[-1]['text'])>PERIOD_TEXT_LIMIT:
        history[-1]['text']='（本期较早行动超出检索预算，完整记录见行动审计归档）\n'+history[-1]['text'][-PERIOD_TEXT_LIMIT+64:]
    # 记忆可以长，但不能只增不减：存档、观察者快照与每次读档都要为它付钱。
    if len(history)>HISTORY_LIMIT:del history[:-HISTORY_LIMIT]


def search(history,query,limit=3,render=None):
    if not isinstance(query,str) or not query.strip():raise ValueError('query 须为非空关键词')
    if type(limit) is not int or not 1<=limit<=8:raise ValueError('limit 须为1到8的整数')
    words=list(dict.fromkeys(query.lower().split()));matches=[]
    for row in history:
        raw=row['text'];folded=raw.lower();positions=[folded.find(word) for word in words]
        if not any(p>=0 for p in positions):continue
        # 旧档的 JSON 行先渲染成散文再返回；匹配可以先认 JSON 字段，但输出不能是 JSON。
        text=_render_legacy(raw,render) if render is not None else raw
        folded=text.lower();positions=[folded.find(word) for word in words]
        found=[p for p in positions if p>=0]
        if not found:
            # 关键字只出现在旧 JSON 字段里（如资源 ID）：给转换后的开头，仍不带 JSON。
            positions=[0];found=[0]
        excerpts=[]
        for pos in sorted(set(found)):
            excerpt=text[max(0,pos-160):pos+320]
            if excerpt not in excerpts:excerpts.append(excerpt)
        matches.append((len(found),row['period'],{'period':row['period'],'matched':[w for w,p in zip(words,positions) if p>=0],'context':'\n…\n'.join(excerpts)}))
    matches.sort(key=lambda item:(item[0],item[1]),reverse=True)
    return deepcopy([row for _,_,row in matches[:limit]])
