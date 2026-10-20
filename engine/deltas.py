"""Lossless JSON changes with stable row identities, shared by player and council."""
from copy import deepcopy


def identity(row):
    if not isinstance(row, dict):return None
    if 'x' in row and 'y' in row and 'owner' in row and 'id' in row:
        return ('unit',row['owner'],row['id'])
    if 'position' in row:return ('position',*row['position'])
    if 'id' in row:return ('id',row['id'])
    return None


def difference(previous, current, path=()):
    """set/remove paths; keyed lists carry upserts/removals and explicit row order."""
    if previous == current:return []
    if isinstance(previous,dict) and isinstance(current,dict):
        changes=[{'path':list((*path,key)),'remove':True} for key in previous if key not in current]
        for key,value in current.items():
            if key in previous:changes.extend(difference(previous[key],value,(*path,key)))
            else:changes.append({'path':list((*path,key)),'set':deepcopy(value)})
        return changes
    if isinstance(previous,list) and isinstance(current,list):
        old={identity(row):row for row in previous};new={identity(row):row for row in current}
        if None not in old and None not in new and len(old)==len(previous) and len(new)==len(current):
            return [{'path':list(path),'rows':{'upsert':[deepcopy(v) for k,v in new.items() if old.get(k)!=v],
                     'remove':[list(k) for k in old if k not in new],
                     'order':[list(k) for k in new] if [k for k in old if k in new]+[k for k in new if k not in old]!=list(new) else None}}]
    return [{'path':list(path),'set':deepcopy(current)}]


def apply(previous, changes):
    result=deepcopy(previous)
    for change in changes:
        path=change['path'];parent=result
        for key in path[:-1]:parent=parent[key]
        if not path:
            if 'set' in change:result=deepcopy(change['set']);continue
            target=result
        else:target=parent.get(path[-1])
        if 'rows' in change:
            rows={identity(row):row for row in target};patch=change['rows']
            for key in patch['remove']:rows.pop(tuple(key),None)
            rows.update((identity(row),deepcopy(row)) for row in patch['upsert'])
            value=[rows[tuple(key)] for key in patch['order']] if patch['order'] is not None else list(rows.values())
            if path:parent[path[-1]]=value
            else:result=value
        elif change.get('remove'):parent.pop(path[-1],None)
        else:parent[path[-1]]=deepcopy(change['set'])
    return result
