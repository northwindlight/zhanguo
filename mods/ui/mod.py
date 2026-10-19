"""A stateless, independently assembled presentation mod."""
from engine.runtime import GameMod
from presentation.i18n import Translator
from presentation.console import ConsoleUI
from presentation.maps import snapshot,render_snapshot,render_svg,expand_message
from presentation.views import player_map,observer_map
from .config import normalize


class UILayer:
    configure=staticmethod(normalize)
    def __init__(self,cfg,runtime=None):
        self.cfg=cfg;self.runtime=runtime;self.tool_layer=getattr(runtime,'tool_layer',None)
        self.text=Translator.from_config(cfg,tool_layer=self.tool_layer)
        self.console=ConsoleUI(cfg,tool_layer=self.tool_layer)
    def translator(self,language='zh-CN',catalog=None):return Translator(language,catalog)
    def observer_map(self,state):return observer_map(state,self.text)
    def map_text(self,observation):return player_map(observation,self.text)
    def map_snapshot(self,observation):return snapshot(observation)
    def map_image(self,observation):return render_snapshot(snapshot(observation),max_px=self.cfg.get('map_max_px',960))
    def map_svg(self,observation):return render_svg(snapshot(observation))
    def expand_message(self,message,*,vision=True):return expand_message(message,vision=vision)


class UIMod(GameMod):
    def mixins(self):return ()
    def presentations(self):return {'ui:create':UILayer,'ui:configure':normalize}
