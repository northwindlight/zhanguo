"""One client action ledger; all mutations go through PlayerSession."""
class Ledger:
    def __init__(self, player, max_actions=None, on_action=None, on_result=None):
        self.player=player;self.max_actions=max_actions;self.acts=[]
        self.on_action=on_action;self.on_result=on_result;self._queries={}
    def query(self,name):
        if name not in self._queries:self._queries[name]=self.player.query(name)
        return self._queries[name]
    def full(self):return self.max_actions is not None and len(self.acts)>=self.max_actions
    def do(self, action, **arguments):
        if self.full():return False
        if self.on_action:self.on_action(action,arguments)
        receipt=self.player.submit(action,arguments);self.acts.append(receipt)
        if receipt['ok']:self._queries.clear()
        if self.on_result:self.on_result(action,arguments,receipt)
        return receipt['ok']
