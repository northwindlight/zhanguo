"""Public strategy references, shared by players and distribution reviewers."""
from pathlib import Path
from engine.runtime import GameMod,RuleSection,Tool

BOOKS=(('basics','基础指南','on_demand',('开局指南','开局','入门')),
       ('economics','经济手册','on_demand',('经济学手册','现金流','复利','滚雪球')),
       ('war','战争手册','on_demand',('军事手册','战术','攻城','战争规划')),
       ('strategy','战略手册','on_demand',('均势','战略规划','僵局')))

class HandbooksMod(GameMod):
    def mixins(self):return ()
    def configure(self,declaration,path):
        super().configure(declaration,path)
        overlay=declaration.get('Handbooks',{}).get('Overlay')
        self.books=[]
        for key,title,prompt,aliases in BOOKS:
            body=Path(__file__).with_name(key+'.md').read_text(encoding='utf-8')
            if overlay:
                supplement=(Path(path).parent/overlay/(('经济学手册' if key=='economics' else title)+'.md')).read_text(encoding='utf-8')
                body+='\n\n'+supplement
            self.books.append((key,title,prompt,aliases,body))
    def tools(self):return (Tool('handbooks',lambda w,a:[{'id':key,'title':title,'prompt_when':prompt} for key,title,prompt,_,_ in self.books],read_only=True,description='策略指南目录。正文用 player_rules(topic=标题) 查询；所有指南正文均按需查询，不自动加入常驻上下文。'),)
    def rule_sections(self,world=None):
        return tuple(RuleSection(title,body,keywords=(key,*aliases),summary='策略参考，具体数值以公开目录和实际回执为准') for key,title,_,aliases,body in self.books)
