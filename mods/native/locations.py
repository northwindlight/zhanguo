"""Equivalent site/place input for all native location tools; no base conversion."""
from copy import deepcopy
from engine.coordinates import coordinates


def location_parameters(parameters):
    parameters=deepcopy(parameters)
    for key in ('site','place'):
        parameters['properties'][key]={'type':'string','description':'地名或坐标串；site 与 place 同义。两者同时填写须指向同一格。'}
    parameters['oneOf']=[
        {'required':['site'],'not':{'anyOf':[{'required':['x']},{'required':['y']}]}},
        {'required':['place'],'not':{'anyOf':[{'required':['site']},{'required':['x']},{'required':['y']}]}},
        {'required':['x','y'],'not':{'anyOf':[{'required':['site']},{'required':['place']}]}}
    ]
    return parameters


def _point(world,reference):
    if not isinstance(reference,str):raise ValueError('site/place 必须是地名或坐标串')
    try:return world.runtime.object_call(world,'native:buildings','site',reference).position
    except KeyError:pass
    point=coordinates(reference)
    if point is not None:return point
    return world.runtime.object_call(world,'native:territory','resolve',reference)


def _arguments(world,arguments,canonical):
    alias='place' if canonical=='site' else 'site'
    if any(key in arguments for key in ('site','place')) and any(key in arguments for key in ('x','y')):
        raise ValueError('地点引用与 x、y 只能选一种；site 和 place 可以互换')
    if alias not in arguments:return arguments
    reference=arguments.pop(alias)
    if canonical in arguments:
        if reference!=arguments[canonical] and _point(world,reference)!=_point(world,arguments[canonical]):
            raise ValueError('site 与 place 指向不同地点，请改为同一格')
    else:arguments[canonical]=reference
    return arguments


def site_arguments(world,actor,arguments):
    return _arguments(world,arguments,'site')


def place_arguments(world,actor,arguments):
    return _arguments(world,arguments,'place')
