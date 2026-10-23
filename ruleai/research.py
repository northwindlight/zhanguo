"""Distribution factory: inject private combat research into the selected strategy."""
import ruleai


def create_client(cfg, *, record=None, services):
    version = cfg.get('rule_ai') or (record['version'] if record else ruleai.DEFAULT_RULE_AI)
    if record and (record.get('kind') != 'rule' or record.get('version') != version
                   or version not in ruleai.versions()):
        raise ValueError('Rule client version mismatch')
    client = ruleai.create(version, record['state'] if record else None,
                           profile=cfg.get('rule_strategy','random'), seed=cfg.get('rule_seed'))
    client.research = True
    client._research = services['combat']
    client.forecast = lambda player, cell, units: client._research('assess', [u['id'] for u in units], *cell)
    return client
