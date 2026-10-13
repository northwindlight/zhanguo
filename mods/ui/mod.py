"""A stateless, independently assembled presentation mod."""
from engine.runtime import GameMod
from .i18n import Translator
from .llm import LLMUI
from .console import ConsoleUI
from .maps import snapshot,render_snapshot,render_svg,expand_message
from .views import player_map
from .config import normalize


class UILayer:
    configure=staticmethod(normalize)
    def __init__(self,cfg):
        self.cfg=cfg;self.text=Translator.from_config(cfg)
        self.console=ConsoleUI(cfg)
    def llm(self,cfg):return LLMUI(cfg)
    def translator(self,language='zh-CN',catalog=None):return Translator(language,catalog)
    def map_text(self,observation):return player_map(observation,self.text)
    def map_snapshot(self,observation):return snapshot(observation)
    def map_image(self,observation):return render_snapshot(snapshot(observation),max_px=self.cfg.get('map_max_px',960))
    def map_svg(self,observation):return render_svg(snapshot(observation))
    def expand_message(self,message,*,vision=True):return expand_message(message,vision=vision)


class UIMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('ui:presentation',)
    def presentations(self):return {'ui:create':UILayer,'ui:configure':normalize}
