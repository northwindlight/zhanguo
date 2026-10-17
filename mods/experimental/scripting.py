"""Saved actor scripts and manual execution through the bound standard API."""
from copy import deepcopy

from engine.player import wire
from engine.runtime import GameMod, ModError, RuleSection
from engine.state import active_unit
from engine.scripting import DEFAULT_LIMITS, Machine, check
from .scripting_api import ScriptingAPI

CONTROLS=frozenset(('script','script_write','script_patch','script_check','script_status','script_run'))


class Programs:
    def __init__(self,limits,actor_check,clock):
        self.limits=dict(limits);self.rows={}
        self._actor_check=actor_check;self._clock=clock;self._domain=None

    def bind_state(self,world,key):
        self._domain=world.runtime.state_owners[key]
        return self

    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use the declared script interface')

    def _actor(self,actor):
        if not self._actor_check(actor):raise ValueError('Actor is not active')

    def _source(self,source):
        if not isinstance(source,str):raise TypeError('Source must be a string')
        if len(source.encode('utf-8'))>self.limits['source']:raise ValueError('Script source size limit exceeded')
        return source

    def _row(self,actor):
        self._actor(actor)
        return self.rows.get(actor,dict(source='',revision=0,last_period=None,result=None))

    def status(self,actor,include_source=False):
        row=self._row(actor)
        result={key:deepcopy(row[key]) for key in ('revision','last_period','result')}
        result.update(available=row['last_period']!=self._clock(),limits=dict(self.limits))
        if include_source:result['source']=row['source']
        return result

    def write(self,actor,source):
        self._write();row=dict(self._row(actor))
        row.update(source=self._source(source),revision=row['revision']+1)
        self.rows[actor]=row
        return {'revision':row['revision'],'bytes':len(source.encode('utf-8'))}

    def patch(self,actor,old,new):
        row=self._row(actor)
        if not isinstance(old,str) or not old or not isinstance(new,str):raise ValueError('Patch requires nonempty old and string new')
        if row['source'].count(old)!=1:raise ValueError('Patch old text must occur exactly once')
        return self.write(actor,row['source'].replace(old,new,1))

    def check(self,actor,source=None):
        row=self._row(actor)
        return check(row['source'] if source is None else source,self.limits)

    def claim(self,actor):
        self._write();row=dict(self._row(actor));period=self._clock()
        if not row['revision']:raise ValueError('Save a script with script_write first')
        if row['last_period']==period:raise ValueError('本回合已执行过脚本，失败也不能重试；下回合再执行')
        row.update(last_period=period,result=None);self.rows[actor]=row
        return {'source':row['source'],'revision':row['revision'],'period':period,'limits':dict(self.limits)}

    def finish(self,actor,result):
        self._write();self.rows[actor]['result']=wire(result)

    def to_save(self):return deepcopy(self.rows)

    def restore(self,data):
        self._write()
        if not isinstance(data,dict):raise ValueError('Invalid script save')
        rows={}
        for actor,row in data.items():
            # Historical actors may no longer be active. Their bounded scripts
            # remain inert, like the rest of the session's historical records.
            if not isinstance(actor,str) or not actor or not isinstance(row,dict) or set(row)!={'source','revision','last_period','result'}:
                raise ValueError('Invalid script record')
            self._source(row['source'])
            if type(row['revision']) is not int or row['revision']<1:raise ValueError('Invalid script revision')
            if row['last_period'] is not None and (type(row['last_period']) is not int or not 0<=row['last_period']<=self._clock()):
                raise ValueError('Invalid script execution period')
            if row['result'] is not None:
                result=row['result']
                if not isinstance(result,dict) or set(result)!={'status','message','output','steps','calls','error','actions','failed_action','revision','period'}:
                    raise ValueError('Invalid script result')
                for field,maximum in (('steps',self.limits['steps']+1),('calls',self.limits['calls']),('actions',self.limits['calls'])):
                    if type(result[field]) is not int or not 0<=result[field]<=maximum:raise ValueError('Invalid script counters')
                if result['actions']>result['calls'] or type(result['period']) is not int or result['period']!=row['last_period'] or type(result['revision']) is not int or not 1<=result['revision']<=row['revision']:
                    raise ValueError('Invalid script execution identity')
                if not isinstance(result['output'],str) or len(result['output'].encode('utf-8'))>self.limits['output']:
                    raise ValueError('Invalid script output')
                error=result['error']
                if error is not None:
                    if not isinstance(error,dict) or set(error)!={'type','message','line','column','source'} or any(not isinstance(error[k],str) for k in ('type','message','source')):
                        raise ValueError('Invalid script error')
                    for k in ('line','column'):
                        if error[k] is not None and (type(error[k]) is not int or not (0 if k=='column' else 1)<=error[k]<=self.limits['source']+1):
                            raise ValueError('Invalid script error position')
                    if len(error['source'].encode('utf-8'))>self.limits['source']:raise ValueError('Invalid error source')
                if result['status']!=('failed' if error else 'effective') or result['message']!=(error['message'] if error else ''):
                    raise ValueError('Invalid script execution status')
                failed=result['failed_action']
                if failed is not None and (not isinstance(failed,dict) or failed.get('actor')!=actor or failed.get('period')!=result['period'] or failed.get('ok') is not False or error is None):
                    raise ValueError('Invalid failed script action')
            rows[actor]=wire(row)
        self.rows=rows


class ScriptingMod(GameMod):
    def configure(self,declaration,path):
        super().configure(declaration,path)
        self.limits=dict(declaration['Limits'])
        if set(self.limits)!=set(DEFAULT_LIMITS) or any(type(v) is not int or v<1 for v in self.limits.values()):
            raise ValueError('Invalid interpreter limits')
        # Implementation bounds protect the parser/host stack and integer work;
        # the distribution may lower or tune the work budget, not remove guards.
        if self.limits['depth']>64 or self.limits['source']>65536 or self.limits['nodes']>8192 or self.limits['integer_bits']>1024:
            raise ValueError('Interpreter structural limits are too large')
    def mixins(self):return ()
    def state_keys(self):return ('scripts',)
    def needs(self):return ('native:government',)
    def provides(self):return ('sandbox:programs',)
    def initialize_world(self,world):
        world.scripts=Programs(self.limits,
            lambda actor:actor in getattr(world,self.runtime.distribution.get('ActorCollection','actors')),
            lambda:self.runtime.object_call(world,'native:government','period'))
    def objects(self,world):return {'sandbox:programs':world.scripts}
    def player_api(self, world):return ScriptingAPI(world)
    def save_state(self,world):return world.scripts.to_save()
    def load_state(self,world,data):world.scripts.restore(data)
    def rule_sections(self,world=None):
        return (RuleSection('Python 沙盒',
            'script_write 保存或重写，script_patch 精确修订，script_check 免费语法/安全检查且不运行。'
            'script_run(inputs={...}) 每国每回合手动执行一次，失败也消耗脚本执行额度，但不阻止普通工具行动；修订、罢免不重置额度。'
            '已成功动作不撤销。api 提供绑定身份的标准 observe/actions/query/submit/events/rules/action_state；'
            '国策同样经过议会。api.submit 拒绝会返回原回执并终止脚本。'
            'inputs 是本次 JSON 参数；变量每次重新创建。支持函数和递归、if/for/while、列表/字典和推导式。'
            f'总预算 {self.limits["steps"]} 计算单位，{self.limits["calls"]} 次 API 调用；计费按语法、数据长度与算法规模，'
            '不用墙钟或高精度计时器。禁止导入、安装库、文件、网络、反射、随机、动态执行和脚本控制工具嵌套。'
            '没有 try/except，不能吞掉预算错误。大型搜索耗尽预算即终止；递归按同一预算计费。'
            '结果保留原错误类型/消息、行列与源码；输出和完整游戏动作审计分开保存。',
            ('脚本','沙盒','python','sandbox'), '保存脚本、免费检查与每回合一次有限计算'),)


class ScriptService:
    version='scripts/1'
    def __init__(self,cfg,*,record=None,services):
        self.programs=services['programs']
        if record is not None and record!={'version':self.version,'state':{}}:raise ValueError('Invalid script service state')
    def to_save(self):return {'version':self.version,'state':{}}
    def submit(self,player,action,arguments,next_submit):
        # 模型侧走 script(action=run) 伞工具；旧名 script_run 仍可被脚本/规则 AI 调用。
        inner=arguments.get('action') if action=='script' and isinstance(arguments,dict) else None
        receipt=next_submit()
        if not (action=='script_run' or (action=='script' and inner=='run')) or not receipt['ok']:return receipt
        claim=receipt['data']
        def invoke(name,args,kwargs):
            if not player.action_state()['active']:raise ValueError('Actor control changed; script handle is no longer active')
            if name in ('query','submit'):
                target=args[0] if args else kwargs.get('name',kwargs.get('action'))
                if isinstance(target,str) and target in CONTROLS:raise ValueError('Nested script control calls are forbidden')
            methods={'observe':player.observe,'actions':player.actions,'query':player.query,
                     'submit':player.submit,'events':player.events,'rules':player.rules,
                     'action_state':player.action_state}
            result=methods[name](*args,**kwargs)
            if name=='actions':result=[r for r in result if r['function']['name'] not in CONTROLS]
            return wire(result)
        machine=Machine(claim['source'],invoke,claim['limits'])
        # Inputs are ordinary detached data; invalid or oversized inputs are
        # execution failures, after the period's allowance has been consumed.
        inputs=arguments.get('inputs') if isinstance(arguments,dict) else None
        machine.env[0]['inputs']={} if inputs is None else inputs
        result=machine.run()
        actions=result.pop('receipts')
        result.update(message=result['error']['message'] if result['error'] else '',
                      actions=len(actions),failed_action=next((r for r in reversed(actions) if not r['ok']),None),
                      revision=claim['revision'],period=claim['period'])
        self.programs('finish',result)
        return {**receipt,'ok':result['error'] is None,'status':result['status'],
                'code':'script_failed' if result['error'] else None,
                'message':result['message'],'data':result}
