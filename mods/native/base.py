"""领域模块的内容装载协议。发行版仅装配，不提供游戏实现。"""
from dataclasses import replace
from importlib import import_module
from pathlib import Path
import re
from engine.runtime import GameMod, RuleSection, Stage, Panel

class DomainMod(GameMod):
    package = ''
    logic_class = ''
    def mixins(self):
        return (getattr(import_module(self.package+'.logic'), self.logic_class),) if self.logic_class else ()
    def install_presentations(self, exports):
        from . import presentation
        presentation.register(exports)
    def command_actors(self):
        module=import_module(self.package+'.logic') if self.logic_class else None
        return {key: value[1] for key,value in getattr(module,'COMMANDS',{}).items()}
    def panels(self):
        views=import_module(self.package+'.views')
        return tuple(Panel(name,getattr(views,handler)) for name,handler in self.declaration.get('Panels',{}).items())
    def state_keys(self): return tuple(self.declaration.get('State', {}).get('Owns', ()))
    def needs(self): return tuple(self.declaration.get('Capabilities', {}).get('Needs', ()))
    def provides(self): return tuple(self.declaration.get('Capabilities', {}).get('Provides', ()))
    def numbers(self):
        return import_module(self.package+'.parameters').VALUES
    def presentations(self):
        return import_module(self.package+'.views').EXPORTS
    def initialize_world(self, world):
        if self.logic_class:
            import_module(self.package+'.initial').initialize(world)
    def tools(self):
        if not self.logic_class or not self.declaration.get("ToolNames"): return ()
        schemas = {s['function']['name']:s['function'] for s in import_module(self.package+'.schemas').definitions()}
        result=[]
        for tool in import_module(self.package+'.tools').definitions():
            schema=schemas.get(tool.name)
            if tool.name=='query' and schema:
                schema['parameters']['properties']['panel']['enum']=sorted(self.runtime.panel_map)
            tool=replace(tool,read_only=tool.name in self.declaration.get('ReadOnlyTools',()))
            result.append(replace(tool, parameters=schema['parameters'], description=schema['description']) if schema else tool)
        return tuple(result)
    def schema_for_actor(self, world, actor, schema): return schema
    def stages(self):
        return tuple(Stage(item['Id'], item['Handler'], hook=item['Hook'],
            needs=tuple(item.get('Needs', ())), provides=tuple(item.get('Provides', ())), scope=item.get('Scope','world'))
            for item in self.declaration.get('Stages', ()))
    def rule_sections(self, world=None):
        cfg=self.declaration.get('Manual', {})
        result=[]
        for filename in cfg.get('Sources', ()):
            text=(self.declaration_path.parent/filename).read_text(encoding='utf-8')
            title, body=text.removeprefix('## ').split('\n',1)
            key=re.sub(r'^[一二三四五六七八九十]+、','',title)
            result.append(RuleSection(title, body.strip(), tuple(k for k,v in cfg.get('Keywords',{}).items() if v==key),
                cfg.get('Summaries',{}).get(key,''), title not in cfg.get('Hidden',())))
        if world is not None:
            for title, function in cfg.get('Lectures',{}).items():
                body=world.runtime.presentations[function]()
                result.append(RuleSection(title,body, tuple(k for k,v in cfg.get('Keywords',{}).items() if v==title),cfg.get('Summaries',{}).get(title,'')))
        return result
