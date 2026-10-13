"""Observer formatting, separate from terminal transport and host mechanics."""
from .i18n import Translator

class ConsoleUI:
    def __init__(self,cfg):self.text=Translator.from_config(cfg)
    def board(self,rows):
        return '\n'.join(self.text('console.board',**row) for row in rows)
