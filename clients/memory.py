"""Search a single client's action prose; never index hidden reasoning."""
import json
from copy import deepcopy

SCHEMA={'type':'function','function':{'name':'memory_search','description':'免费检索自己的历史行动、发言和回执正文，排除思考过程。关键词空格分隔，全命中优先，返回周期和邻近上下文；压缩上下文后仍可检索，不能读取其他国家记忆。',
 'parameters':{'type':'object','properties':{'query':{'type':'string','minLength':1},'limit':{'type':'integer','minimum':1,'maximum':8,'default':3}},'required':['query'],'additionalProperties':False}}}


def action_text(assistant,calls,receipts):
    parts=[assistant['content']] if isinstance(assistant.get('content'),str) and assistant['content'] else []
    for call,receipt in zip(calls,receipts):
        fn=call.get('function',{})
        if fn.get('name')=='memory_search':continue
        parts.append(json.dumps({'action':fn.get('name'),'arguments':fn.get('arguments'),'receipt':receipt},ensure_ascii=False,separators=(',',':')))
    return '\n'.join(parts)


def record(history,period,text):
    if not text:return
    if history and history[-1]['period']==period:history[-1]['text']+='\n'+text
    else:history.append({'period':period,'text':text})


def search(history,query,limit=3):
    if not isinstance(query,str) or not query.strip():raise ValueError('query 须为非空关键词')
    if type(limit) is not int or not 1<=limit<=8:raise ValueError('limit 须为1到8的整数')
    words=list(dict.fromkeys(query.lower().split()));matches=[]
    for row in history:
        text=row['text'];folded=text.lower();positions=[folded.find(word) for word in words]
        found=[p for p in positions if p>=0]
        if not found:continue
        # Return matching excerpts, including neighboring prose, rather than a
        # beginning-only truncation that might cut away the actual evidence.
        excerpts=[]
        for pos in sorted(set(found)):
            excerpt=text[max(0,pos-160):pos+320]
            if excerpt not in excerpts:excerpts.append(excerpt)
        matches.append((len(found),row['period'],{'period':row['period'],'matched':[w for w,p in zip(words,positions) if p>=0],'context':'\n…\n'.join(excerpts)}))
    matches.sort(key=lambda item:(item[0],item[1]),reverse=True)
    return deepcopy([row for _,_,row in matches[:limit]])
