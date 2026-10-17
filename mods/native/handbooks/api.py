"""Player-facing handbook catalog API."""


class HandbooksAPI:
    def __init__(self, mod):
        self.mod = mod

    def books(self, actor):
        return [{'id': key, 'title': title, 'prompt_when': prompt} for key, title, prompt, _, _ in self.mod.books]
