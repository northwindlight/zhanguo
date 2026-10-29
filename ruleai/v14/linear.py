"""Small sparse MILP builder; the optimization algorithm is HiGHS, not heuristics."""
from dataclasses import dataclass
import math


class Expr:
    def __init__(self, terms=None, constant=0):
        self.terms = terms or {}
        self.constant = constant

    def __add__(self, other):
        other = expr(other)
        terms = dict(self.terms)
        for key, value in other.terms.items():
            terms[key] = terms.get(key, 0)+value
        return Expr({k:v for k,v in terms.items() if v}, self.constant+other.constant)

    __radd__ = __add__

    def __neg__(self):
        return self*-1

    def __sub__(self, other):
        return self+-expr(other)

    def __rsub__(self, other):
        return expr(other)+-self

    def __mul__(self, number):
        return Expr({k:v*number for k,v in self.terms.items()}, self.constant*number)

    __rmul__ = __mul__

    def value(self, values):
        return self.constant+sum(values[k]*v for k,v in self.terms.items())


def expr(value):
    return value if isinstance(value, Expr) else Expr(constant=value)


@dataclass
class Solution:
    values: object
    status: str
    objective: float | None
    gap: float | None
    bound: float | None
    nodes: int | None
    message: str


class Model:
    def __init__(self):
        self.names, self.lower, self.upper, self.integer = [], [], [], []
        self.rows, self.row_lower, self.row_upper = [], [], []

    def var(self, name, lower=0, upper=math.inf, integer=False):
        key = len(self.names)
        self.names.append(name);self.lower.append(lower);self.upper.append(upper);self.integer.append(int(integer))
        return Expr({key:1})

    def limit(self, value, lower=-math.inf, upper=math.inf):
        value = expr(value)
        self.rows.append(value.terms)
        self.row_lower.append(lower-value.constant);self.row_upper.append(upper-value.constant)

    def equal(self, a, b):
        self.limit(expr(a)-b, 0, 0)

    def solve(self, objective, *, node_limit=20, gap=0, time_limit=None):
        try:
            import numpy as np
            from scipy.optimize import Bounds, LinearConstraint, milp
            from scipy.sparse import csc_matrix
        except ImportError as error:
            raise RuntimeError('v14联合规划需要NumPy/SciPy：请在虚拟环境安装 requirements-ruleai.txt') from error
        indices, columns, data = [], [], []
        for row, terms in enumerate(self.rows):
            for column, value in terms.items():
                indices.append(row);columns.append(column);data.append(value)
        matrix = csc_matrix((data,(indices,columns)),shape=(len(self.rows),len(self.names)))
        objective = expr(objective)
        costs = np.zeros(len(self.names))
        for key, value in objective.terms.items():
            costs[key] = -value
        options={'node_limit':node_limit,'mip_rel_gap':gap,'presolve':True}
        if time_limit is not None:options['time_limit']=time_limit
        result = milp(costs, integrality=np.array(self.integer),
            bounds=Bounds(self.lower,self.upper),
            constraints=LinearConstraint(matrix,self.row_lower,self.row_upper),
            options=options)
        values = result.x
        # Accept a bounded incumbent only after independent row/integer checks.
        if values is not None:
            valid = (np.all(values>=np.asarray(self.lower)-1e-5) and
                     np.all(values<=np.asarray(self.upper)+1e-5) and
                     all(not whole or abs(values[i]-round(values[i]))<=1e-5 for i,whole in enumerate(self.integer)))
            activity = matrix@values
            valid = valid and np.all(activity>=np.asarray(self.row_lower)-1e-5) and np.all(activity<=np.asarray(self.row_upper)+1e-5)
            if not valid:
                values = None
        status = {0:'optimal',1:'limited',2:'infeasible',3:'unbounded',4:'solver_error'}.get(result.status,'solver_error')
        if status=='optimal' and getattr(result,'mip_gap',0)>1e-7:
            status='tolerance_met'
        if status=='solver_error' and 'limit reached' in str(result.message).lower():
            status='limited'
        elif status=='solver_error' and values is not None:
            status='feasible_unclassified'
        return Solution(values,status,float(objective.value(values)) if values is not None else None,
            float(result.mip_gap) if getattr(result,'mip_gap',None) is not None else None,
            -float(result.mip_dual_bound)+objective.constant if getattr(result,'mip_dual_bound',None) is not None else None,
            int(result.mip_node_count) if getattr(result,'mip_node_count',None) is not None else None,str(result.message))
