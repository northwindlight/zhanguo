"""Metered Python subset. Never eval/exec code or expose Python host objects."""
import ast
from dataclasses import dataclass
import math
import operator


class SandboxLimitError(Exception):pass
class SandboxSyntaxError(Exception):pass
class SandboxActionError(Exception):pass


DEFAULT_LIMITS = dict(steps=12000, calls=24, source=16384, nodes=2048,
                      items=4096, string=65536, value_bytes=1048576,
                      output=4096, depth=48, integer_bits=256)
BUILTINS = frozenset(('abs','all','any','bool','dict','enumerate','float','int',
                      'len','list','max','min','print','range','reversed','round',
                      'sorted','str','sum','tuple','zip'))
METHODS = frozenset(('get','items','keys','values','append','extend','pop','copy',
                     'count','index','sort','reverse','lower','upper','strip',
                     'split','join','startswith','endswith','replace'))
API_METHODS = frozenset(('observe','actions','query','submit','events','rules','action_state'))
ALLOWED = (ast.Module,ast.Expr,ast.Assign,ast.AugAssign,ast.If,ast.For,ast.While,
           ast.Break,ast.Continue,ast.Pass,ast.FunctionDef,ast.Return,
           ast.Name,ast.Constant,ast.List,ast.Tuple,ast.Dict,ast.Subscript,ast.Slice,
           ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.IfExp,ast.Call,
           ast.Attribute,ast.ListComp,ast.DictComp,ast.comprehension,
           ast.arguments,ast.arg,ast.keyword,ast.Load,ast.Store,
           ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Pow,
           ast.UAdd,ast.USub,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Lt,
           ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.Is,ast.IsNot)
BIN = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,
       ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,
       ast.Mod:operator.mod,ast.Pow:operator.pow}
CMP = {ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,
       ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,
       ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b,
       ast.Is:lambda a,b:a is b,ast.IsNot:lambda a,b:a is not b}


def error_info(exc, source, line=None, column=None):
    if isinstance(exc,SyntaxError):line,column=exc.lineno,exc.offset
    lines=source.splitlines()
    return {'type':type(exc).__name__,'message':str(exc),'line':line,
            'column':column,'source':lines[line-1] if line and line<=len(lines) else ''}


def parse(source, limits=None):
    limits=limits or DEFAULT_LIMITS
    if not isinstance(source,str):raise TypeError('Source must be a string')
    if len(source.encode('utf-8'))>limits['source']:raise SandboxLimitError('Source size limit exceeded')
    tree=ast.parse(source,filename='<script>',mode='exec')
    nodes=list(ast.walk(tree))
    if len(nodes)>limits['nodes']:raise SandboxLimitError('Syntax tree size limit exceeded')
    def fail(node,message):
        exc=SandboxSyntaxError(message)
        exc.line=getattr(node,'lineno',None);exc.column=getattr(node,'col_offset',0)+1
        raise exc
    for node in nodes:
        if not isinstance(node,ALLOWED):fail(node,f'Unsupported syntax: {type(node).__name__}')
        if isinstance(node,ast.Name) and node.id.startswith('__'):fail(node,'Private names are forbidden')
        if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store) and node.id in BUILTINS|{'api'}:
            fail(node,'Reserved name')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('_') or node.attr not in METHODS|API_METHODS):
            fail(node,f'Unsupported attribute: {node.attr}')
        if isinstance(node,ast.Attribute) and isinstance(node.ctx,ast.Store):fail(node,'Attribute assignment is unsupported')
        if isinstance(node,ast.FunctionDef):
            a=node.args
            if node.decorator_list or node.returns or a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
                fail(node,'Decorators, annotations and variadic parameters are unsupported')
            if node.name in BUILTINS|{'api'} or node.name.startswith('__'):fail(node,'Reserved function name')
        if isinstance(node,ast.arg) and (node.annotation or node.arg.startswith('__') or node.arg in BUILTINS|{'api'}):
            fail(node,'Invalid parameter')
        if isinstance(node,ast.keyword) and node.arg is None:fail(node,'Keyword unpacking is unsupported')
        if isinstance(node,ast.Dict) and any(k is None for k in node.keys):fail(node,'Dictionary unpacking is unsupported')
        if isinstance(node,ast.comprehension) and node.is_async:fail(node,'Async is unsupported')
        if isinstance(node,ast.Constant):
            v=node.value
            if type(v) not in (type(None),bool,int,float,str):fail(node,'Unsupported literal')
            if type(v) is int and v.bit_length()>limits['integer_bits']:fail(node,'Integer size limit exceeded')
            if type(v) is float and not math.isfinite(v):fail(node,'Nonfinite literal')
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in BUILTINS|{'api'}:fail(node,'Reserved name')
    def nesting(node,depth=0):
        if depth>limits['depth']:fail(node,'Syntax nesting limit exceeded')
        for child in ast.iter_child_nodes(node):nesting(child,depth+1)
    nesting(tree)
    def control(node,in_function=False,in_loop=False):
        if isinstance(node,ast.Return) and not in_function:fail(node,'return outside function')
        if isinstance(node,(ast.Break,ast.Continue)) and not in_loop:fail(node,'loop control outside loop')
        if isinstance(node,ast.FunctionDef):
            for child in node.body:control(child,True,False)
        elif isinstance(node,(ast.For,ast.While)):
            for child in node.body:control(child,in_function,True)
            for child in node.orelse:control(child,in_function,in_loop)
        else:
            for child in ast.iter_child_nodes(node):control(child,in_function,in_loop)
    control(tree)
    return tree


@dataclass
class Function:
    node: object
    closure: list
    defaults: list


@dataclass(frozen=True)
class Builtin:
    name: str


@dataclass(frozen=True)
class Method:
    receiver: object
    name: str


class Return(Exception):
    def __init__(self,value):self.value=value
class Break(Exception):pass
class Continue(Exception):pass
API = object()


class Machine:
    def __init__(self,source,api,limits=None):
        self.source,self.api=source,api
        self.limits=dict(limits or DEFAULT_LIMITS)
        self.steps=0;self.calls=0;self.line=None;self.column=None;self.depth=0
        self.output='';self.receipts=[]
        self.env=[{**{name:Builtin(name) for name in BUILTINS},'api':API}]

    def charge(self,amount=1):
        self.steps=min(self.steps+amount,self.limits['steps']+1)
        if self.steps>self.limits['steps']:raise SandboxLimitError('Computation budget exceeded')

    def value(self,value,depth=0,path=None):
        """Validate and meter JSON-like data before it reaches Python operations."""
        self.charge()
        if depth>self.limits['depth']:raise SandboxLimitError('Value nesting limit exceeded')
        if isinstance(value,(Function,Builtin,Method)) or value is API:return 8
        if value is None or type(value) is bool:return 8
        if type(value) is int:
            if value.bit_length()>self.limits['integer_bits']:raise SandboxLimitError('Integer size limit exceeded')
            return 32
        if type(value) is float:
            if not math.isfinite(value):raise OverflowError('Nonfinite arithmetic result')
            return 8
        if type(value) is str:
            size=len(value.encode('utf-8'));self.charge((size+63)//64)
            if size>self.limits['string']:raise SandboxLimitError('String size limit exceeded')
            return size+32
        if type(value) in (list,tuple,dict,range):
            if len(value)>self.limits['items']:raise SandboxLimitError('Container size limit exceeded')
            if type(value) is range:return 32
            path=set() if path is None else path
            if id(value) in path:raise ValueError('Cyclic containers are unsupported')
            path.add(id(value));size=32
            for item in (value.items() if type(value) is dict else value):
                size+=self.value(item,depth+1,path)
                if size>self.limits['value_bytes']:raise SandboxLimitError('Value size limit exceeded')
            path.remove(id(value));return size
        raise TypeError('Unsupported sandbox value')

    def checked(self,value):self.value(value);return value

    def plain(self,value,depth=0):
        self.charge()
        if depth>self.limits['depth']:raise SandboxLimitError('Value nesting limit exceeded')
        if value is API or isinstance(value,(Function,Builtin,Method)):
            raise TypeError('Host handles are not data')
        if type(value) in (list,tuple,dict):
            for v in (value.items() if type(value) is dict else value):self.plain(v,depth+1)
        return value

    def sequence(self,value):
        if type(value) not in (list,tuple,dict,str,range):raise TypeError(f"'{type(value).__name__}' object is not iterable")
        self.value(value)
        return value

    def lookup(self,name):
        for scope in self.env:
            if name in scope:return scope[name]
        raise NameError(f"name '{name}' is not defined")

    def assign(self,target,value):
        if isinstance(target,ast.Name):
            if target.id in BUILTINS|{'api'}:raise SandboxSyntaxError('Reserved name')
            self.env[0][target.id]=value
        elif isinstance(target,(ast.List,ast.Tuple)):
            seq=self.sequence(value)
            if len(target.elts)!=len(seq):raise ValueError('Unpacking length mismatch')
            for node,item in zip(target.elts,seq):self.assign(node,item)
        elif isinstance(target,ast.Subscript):
            obj=self.eval(target.value);key=self.eval(target.slice)
            if not isinstance(key,slice):self.plain(key)
            if type(obj) not in (dict,list):raise TypeError('Assignment requires dict or list')
            # Stage a copy to ensure cycles/size failures never mutate live data.
            self.charge(len(obj)+1)
            candidate=obj.copy();candidate[key]=value;self.value(candidate)
            if self.contains(value,obj):raise ValueError('Cyclic containers are unsupported')
            obj[key]=value
        else:raise SandboxSyntaxError('Unsupported assignment target')

    def contains(self,value,target,depth=0):
        self.charge()
        if value is target:return True
        if depth>self.limits['depth']:raise SandboxLimitError('Value nesting limit exceeded')
        return type(value) in (list,tuple,dict) and any(self.contains(v,target,depth+1) for v in
            (value.values() if type(value) is dict else value))

    def eval(self,node):
        self.charge();self.depth+=1
        self.line=getattr(node,'lineno',self.line);self.column=getattr(node,'col_offset',0)+1
        try:
            if self.depth>self.limits['depth']:raise SandboxLimitError('Call depth limit exceeded')
            return self._eval(node)
        finally:self.depth-=1

    def _eval(self,n):
        if isinstance(n,ast.Constant):return self.checked(n.value)
        if isinstance(n,ast.Name):return self.lookup(n.id)
        if isinstance(n,(ast.List,ast.Tuple)):
            values=[self.eval(v) for v in n.elts]
            return self.checked(tuple(values) if isinstance(n,ast.Tuple) else values)
        if isinstance(n,ast.Dict):
            self.charge(len(n.keys)**2)
            result={}
            for k,v in zip(n.keys,n.values):
                key=self.plain(self.eval(k));result[key]=self.eval(v)
            return self.checked(result)
        if isinstance(n,ast.Slice):return slice(*(self.eval(v) if v else None for v in (n.lower,n.upper,n.step)))
        if isinstance(n,ast.Subscript):
            obj=self.eval(n.value);key=self.eval(n.slice)
            if not isinstance(key,slice):self.plain(key)
            if type(obj) not in (list,tuple,dict,str,range):raise TypeError('Object is not subscriptable')
            self.value(obj);self.line=n.lineno;self.column=n.col_offset+1
            return self.checked(obj[key])
        if isinstance(n,ast.BinOp):
            a,b=self.eval(n.left),self.eval(n.right)
            self.line=n.lineno;self.column=n.col_offset+1
            return self.binary(n.op,a,b)
        if isinstance(n,ast.UnaryOp):
            v=self.eval(n.operand)
            if isinstance(n.op,ast.Not):return not self.truth(v)
            if type(v) not in (int,float,bool):raise TypeError('Unary arithmetic requires a number')
            return self.checked(-v if isinstance(n.op,ast.USub) else +v)
        if isinstance(n,ast.BoolOp):
            for node in n.values:
                v=self.eval(node)
                if isinstance(n.op,ast.And) and not self.truth(v) or isinstance(n.op,ast.Or) and self.truth(v):return v
            return v
        if isinstance(n,ast.IfExp):return self.eval(n.body if self.truth(self.eval(n.test)) else n.orelse)
        if isinstance(n,ast.Compare):
            a=self.eval(n.left)
            for op,node in zip(n.ops,n.comparators):
                b=self.eval(node);self.value(a);self.value(b);self.plain(a);self.plain(b)
                if isinstance(op,(ast.Is,ast.IsNot)) and not (a is None or type(a) is bool) and not (b is None or type(b) is bool):
                    raise TypeError('Identity comparison requires None or bool')
                if not CMP[type(op)](a,b):return False
                a=b
            return True
        if isinstance(n,ast.Attribute):
            obj=self.eval(n.value)
            if obj is API:
                if n.attr not in API_METHODS:raise AttributeError(n.attr)
            elif n.attr not in METHODS or type(obj) not in (dict,list,tuple,str):raise AttributeError(n.attr)
            return Method(obj,n.attr)
        if isinstance(n,ast.Call):
            fn=self.eval(n.func);args=[self.eval(v) for v in n.args]
            kwargs={v.arg:self.eval(v.value) for v in n.keywords}
            # Report the actual call/operator line, not the last argument's line.
            self.line=n.lineno;self.column=n.col_offset+1
            return self.call(fn,args,kwargs)
        if isinstance(n,(ast.ListComp,ast.DictComp)):
            result=[] if isinstance(n,ast.ListComp) else {}
            self.env.insert(0,{})
            try:
                def visit(index):
                    if index==len(n.generators):
                        if isinstance(n,ast.ListComp):result.append(self.eval(n.elt))
                        else:
                            self.charge(len(result)+1)
                            result[self.plain(self.eval(n.key))]=self.eval(n.value)
                        self.value(result);return
                    gen=n.generators[index]
                    for value in self.sequence(self.eval(gen.iter)):
                        self.charge();self.assign(gen.target,value)
                        if all(self.truth(self.eval(t)) for t in gen.ifs):visit(index+1)
                visit(0)
            finally:self.env.pop(0)
            return result
        raise SandboxSyntaxError(f'Unsupported expression: {type(n).__name__}')

    def truth(self,value):
        self.value(value)
        if value is API or isinstance(value,(Function,Builtin,Method)):raise TypeError('Callable truth values are unsupported')
        return bool(value)

    def binary(self,op,a,b):
        self.value(a);self.value(b);self.plain(a);self.plain(b)
        if isinstance(op,ast.Mod) and type(a) is str:
            raise SandboxSyntaxError('String formatting is unsupported; use str or print')
        if isinstance(op,ast.Add) and type(a) in (str,list,tuple) and type(b) is type(a):
            size=len(a)+len(b)
            if size>(self.limits['string'] if type(a) is str else self.limits['items']):
                raise SandboxLimitError('Concatenation size limit exceeded')
            self.charge(size)
        if isinstance(op,ast.Mult):
            seq,count=(a,b) if type(a) in (str,list,tuple) else (b,a)
            if type(seq) in (str,list,tuple) and type(count) is int:
                size=len(seq)*max(0,count)
                if size>(self.limits['string'] if type(seq) is str else self.limits['items']):
                    raise SandboxLimitError('Multiplication size limit exceeded')
                # Aliased nested elements still require work proportional to
                # their full expanded representation, before allocating.
                self.charge(max(0,count)*max(1,self.value(seq)))
        if isinstance(op,ast.Pow):
            if type(b) is not int:raise TypeError('Exponent must be an integer in this Python subset')
            if abs(b)>256:raise SandboxLimitError('Exponent size limit exceeded')
            if type(a) is int and type(b) is int and b>0 and a.bit_length()*b>self.limits['integer_bits']:
                raise SandboxLimitError('Integer size limit exceeded')
            self.charge(int(abs(b))+1)
            if type(a) is float or b<0:
                # Fixed multiply/divide sequence instead of platform libm pow.
                base=1/a if b<0 else a
                power=abs(b);result=1.0
                while power:
                    self.charge()
                    if power&1:result=self.checked(result*base)
                    power//=2
                    if power:base=self.checked(base*base)
                return result
        return self.checked(BIN[type(op)](a,b))

    def call(self,fn,args,kwargs):
        self.charge()
        if isinstance(fn,Function):
            names=[a.arg for a in fn.node.args.args]
            if len(args)>len(names) or set(kwargs)-set(names):raise TypeError('Invalid function arguments')
            scope=dict(zip(names,args))
            if set(scope)&set(kwargs):raise TypeError('Multiple values for argument')
            scope.update(kwargs)
            for name,value in zip(names[len(names)-len(fn.defaults):],fn.defaults):scope.setdefault(name,value)
            if set(scope)!=set(names):raise TypeError('Missing required argument')
            previous=self.env;self.env=[scope,*fn.closure]
            try:
                try:self.block(fn.node.body)
                except Return as exc:return exc.value
                return None
            finally:self.env=previous
        if isinstance(fn,Method):return self.method(fn,args,kwargs)
        if not isinstance(fn,Builtin):raise TypeError('Object is not callable')
        name=fn.name
        for a in args:self.value(a)
        for a in kwargs.values():self.value(a)
        if name=='print':
            if set(kwargs)-{'sep','end'}:raise TypeError('Unsupported print keyword')
            sep,end=kwargs.get('sep',' '),kwargs.get('end','\n')
            if type(sep) is not str or type(end) is not str:raise TypeError('sep/end must be strings')
            text=sep.join(self.text(a) for a in args)+end
            if len((self.output+text).encode('utf-8'))>self.limits['output']:raise SandboxLimitError('Output size limit exceeded')
            self.output+=text;self.charge(len(text));return None
        if name in ('sorted','min','max'):
            key=kwargs.pop('key',None);reverse=kwargs.pop('reverse',False) if name=='sorted' else False
            if kwargs:raise TypeError('Unsupported keyword')
            seq=list(self.sequence(args[0])) if len(args)==1 else list(args)
            if name=='sorted' and len(args)!=1:raise TypeError('sorted expects one iterable')
            self.charge(len(seq)*max(1,len(seq).bit_length()))
            keyed=[(self.call(key,[v],{}) if key is not None else v,i,v) for i,v in enumerate(seq)]
            key_cost=1
            for k,_,_ in keyed:
                self.plain(k);key_cost=max(key_cost,(self.value(k)+63)//64)
            self.charge(len(seq)*max(1,len(seq).bit_length())*key_cost)
            if name=='sorted':return self.checked([v for _,_,v in sorted(keyed,key=lambda x:x[0],reverse=reverse)])
            if not keyed:raise ValueError(f'{name}() arg is an empty sequence')
            return (min if name=='min' else max)(keyed,key=lambda x:x[0])[2]
        if name in ('enumerate','zip','reversed'):
            if kwargs:raise TypeError('Unsupported keyword')
            if name=='enumerate':
                seq=self.sequence(args[0]);start=args[1] if len(args)>1 else 0
                if len(args)>2 or type(start) is not int:raise TypeError('Invalid enumerate arguments')
                return self.checked(list(enumerate(seq,start)))
            if name=='reversed':return self.checked(list(reversed(self.sequence(args[0]))))
            sequences=[self.sequence(v) for v in args];return self.checked(list(zip(*sequences)))
        if name in ('all','any','sum'):
            if kwargs:raise TypeError('Unsupported keyword')
            seq=self.sequence(args[0]);self.charge(len(seq))
            if name in ('all','any'):return (all if name=='all' else any)(self.truth(v) for v in seq)
            total=args[1] if len(args)>1 else 0
            if len(args)>2:raise TypeError('Invalid sum arguments')
            for v in seq:total=self.binary(ast.Add(),total,v)
            return total
        if name=='str':
            if kwargs or len(args)>1:raise TypeError('Invalid str arguments')
            return self.checked(self.text(args[0]) if args else '')
        if name in ('list','tuple','dict'):
            if args:self.sequence(args[0])
            if name=='dict' and args and type(args[0]) is not dict:self.charge(len(args[0])**2)
            if name=='dict':
                for v in args:self.plain(v)
                for v in kwargs.values():self.plain(v)
        if name in ('int','float','bool','abs','round','len','range'):
            for v in args:self.plain(v)
            for v in kwargs.values():self.plain(v)
        if name=='int' and args and type(args[0]) is str and len(args[0])>80:
            raise SandboxLimitError('Integer input size limit exceeded')
        # Every operand is already a checked primitive; no user-defined hooks.
        functions=dict(abs=abs,bool=bool,dict=dict,float=float,int=int,len=len,list=list,
                       range=range,round=round,tuple=tuple)
        if name not in functions:raise TypeError('Unsupported builtin')
        result=functions[name](*args,**kwargs)
        return self.checked(result)

    def text(self,value):
        self.value(value);self.plain(value)
        return str(value)

    def method(self,fn,args,kwargs):
        obj,name=fn.receiver,fn.name
        if obj is API:
            for v in args:self.plain(v);self.value(v)
            for v in kwargs.values():self.plain(v);self.value(v)
            if self.calls>=self.limits['calls']:raise SandboxLimitError('API call budget exceeded')
            self.charge(100)
            self.calls+=1
            result=self.api(name,args,kwargs)
            # Action receipts survive even if subsequent computation hits a limit.
            if name=='submit':self.receipts.append(result)
            result=self.checked(result)
            if name=='submit' and not result['ok']:raise SandboxActionError(result['message'])
            return result
        self.value(obj)
        for v in args:self.value(v)
        for v in kwargs.values():self.value(v)
        allowed={dict:{'get','items','keys','values','pop','copy'},
                 list:{'append','extend','pop','copy','count','index','sort','reverse'},
                 tuple:{'count','index'},
                 str:{'lower','upper','strip','split','join','startswith','endswith','replace','count','index'}}
        if name not in allowed[type(obj)]:raise AttributeError(name)
        if type(obj) is dict and name in ('get','pop') and args:self.plain(args[0])
        if type(obj) is list and name=='sort':
            if args:raise TypeError('sort() takes no positional arguments')
            candidate=self.call(Builtin('sorted'),[obj],dict(kwargs));obj[:]=candidate;return None
        if type(obj) in (list,dict) and name in ('append','extend','pop','reverse'):
            candidate=obj.copy()
            if name=='extend':self.sequence(args[0])
            for v in args:
                if self.contains(v,obj):raise ValueError('Cyclic containers are unsupported')
            result=getattr(candidate,name)(*args,**kwargs);self.value(candidate)
            if type(obj) is list:obj[:]=candidate
            else:obj.clear();obj.update(candidate)
            return self.checked(result)
        if name=='join':
            seq=self.sequence(args[0]);self.charge(sum(len(v) if type(v) is str else 1 for v in seq))
            if sum(len(v) if type(v) is str else 0 for v in seq)+len(obj)*max(0,len(seq)-1)>self.limits['string']:
                raise SandboxLimitError('String size limit exceeded')
        if name=='replace' and len(args)>=2:
            if type(args[0]) is not str or type(args[1]) is not str:raise TypeError('replace expects strings')
            self.charge(len(obj)*(1+len(args[0])))
            count=obj.count(args[0]) if len(args)<3 or args[2]<0 else min(obj.count(args[0]),args[2])
            if len(obj)+count*max(0,len(args[1])-len(args[0]))>self.limits['string']:
                raise SandboxLimitError('String size limit exceeded')
        self.charge(len(obj)*(1+sum(len(v) for v in args if type(v) is str)))
        result=getattr(obj,name)(*args,**kwargs)
        if name in ('items','keys','values'):result=list(result)
        return self.checked(result)

    def block(self,statements):
        for node in statements:
            self.charge();self.line=node.lineno;self.column=node.col_offset+1
            if isinstance(node,ast.Expr):self.eval(node.value)
            elif isinstance(node,ast.Assign):
                v=self.eval(node.value)
                for target in node.targets:self.assign(target,v)
            elif isinstance(node,ast.AugAssign):self.assign(node.target,self.binary(node.op,self.eval(node.target),self.eval(node.value)))
            elif isinstance(node,ast.If):self.block(node.body if self.truth(self.eval(node.test)) else node.orelse)
            elif isinstance(node,(ast.For,ast.While)):
                broke=False
                iterator=iter(self.sequence(self.eval(node.iter))) if isinstance(node,ast.For) else None
                while True:
                    self.charge()
                    if iterator is not None:
                        try:v=next(iterator)
                        except StopIteration:break
                        self.assign(node.target,v)
                    elif not self.truth(self.eval(node.test)):break
                    try:self.block(node.body)
                    except Continue:continue
                    except Break:broke=True;break
                if not broke:self.block(node.orelse)
            elif isinstance(node,ast.FunctionDef):
                self.env[0][node.name]=Function(node,list(self.env),[self.eval(v) for v in node.args.defaults])
            elif isinstance(node,ast.Return):raise Return(self.eval(node.value) if node.value else None)
            elif isinstance(node,ast.Break):raise Break()
            elif isinstance(node,ast.Continue):raise Continue()
            elif not isinstance(node,ast.Pass):raise SandboxSyntaxError('Unsupported statement')

    def run(self):
        try:
            tree=parse(self.source,self.limits)
            self.value(self.env[0].get('inputs',{}))
            self.plain(self.env[0].get('inputs',{}))
            self.block(tree.body)
            error=None
        except (Return,Break,Continue):
            error=error_info(SandboxSyntaxError('Control flow outside its enclosing block'),self.source,self.line,self.column)
        except Exception as exc:
            error=error_info(exc,self.source,getattr(exc,'line',self.line),getattr(exc,'column',self.column))
        return {'status':'failed' if error else 'effective','output':self.output,
                'steps':self.steps,'calls':self.calls,'error':error,'receipts':self.receipts}


def check(source,limits=None):
    try:
        parse(source,limits)
        return {'valid':True,'error':None}
    except Exception as exc:
        return {'valid':False,'error':error_info(exc,source if isinstance(source,str) else '',
                       getattr(exc,'line',None),getattr(exc,'column',None))}
