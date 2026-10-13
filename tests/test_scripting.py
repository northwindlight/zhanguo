"""Deterministic interpreter acceptance; no native execution of user source."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from engine.scripting import Machine, check, DEFAULT_LIMITS

ROOT=Path(__file__).resolve().parents[1]


def no_api(*args):raise AssertionError('Unexpected API call')


class TestPythonSubset(unittest.TestCase):
    def run_code(self,source,**limits):
        return Machine(source,no_api,{**DEFAULT_LIMITS,**limits}).run()

    def test_interpolation_functions_recursion_and_container_helpers(self):
        source='''
def interpolate(xs, ys, x):
    for i in range(1, len(xs)):
        if x <= xs[i]:
            return ys[i-1] + (ys[i]-ys[i-1]) * (x-xs[i-1]) / (xs[i]-xs[i-1])
    return ys[-1]
def fact(n):
    if n < 2:
        return 1
    return n * fact(n-1)
points = [(0, 1.5), (2, 1.7), (4, 1.8)]
xs, ys = zip(points[0], points[1], points[2])
print(round(interpolate(xs, ys, 3), 3), fact(5))
print(sorted([3, 1, 2]), sum([1, 2, 3]))
a = [i*i for i in range(5) if i % 2 == 0]
d = {str(i): i for i in a}
a.append(25)
print(a, d.get('16'), ' x '.strip())
'''
        result=self.run_code(source)
        self.assertIsNone(result['error'],result)
        self.assertEqual(result['output'],'1.75 120\n[1, 2, 3] 6\n[0, 4, 16, 25] 16 x\n')
        self.assertLess(result['steps'],DEFAULT_LIMITS['steps'])

    def test_same_inputs_always_have_same_computation_cost(self):
        source='''
def fib(n):
    if n < 2:
        return n
    return fib(n-1) + fib(n-2)
print(fib(8))
'''
        a,b=self.run_code(source),self.run_code(source)
        self.assertEqual(a,b)
        self.assertEqual(a['output'],'21\n')
        source=source.replace('fib(8)','fib(16)')
        result=self.run_code(source)
        self.assertEqual(result['error']['type'],'SandboxLimitError')
        self.assertEqual(result['error']['message'],'Computation budget exceeded')

    def test_infinite_loop_and_recursive_search_cannot_escape_budgets(self):
        for source in ('while True:\n    pass','def f():\n    return f()\nf()'):
            result=self.run_code(source)
            self.assertEqual(result['error']['type'],'SandboxLimitError')
            self.assertLessEqual(result['steps'],DEFAULT_LIMITS['steps']+1)

    def test_syntax_and_original_runtime_errors_keep_source_positions(self):
        syntax=check('if :\n pass')
        self.assertFalse(syntax['valid'])
        self.assertEqual(syntax['error']['type'],'SyntaxError')
        self.assertEqual(syntax['error']['line'],1)
        result=self.run_code('x = 1\ny = 1 / 0\nprint(y)')
        self.assertEqual(result['error']['type'],'ZeroDivisionError')
        self.assertEqual(result['error']['message'],'division by zero')
        self.assertEqual(result['error']['line'],2)
        self.assertEqual(result['error']['source'],'y = 1 / 0')
        for source in ('return 1','break','continue','def f():\n    break'):
            self.assertFalse(check(source)['valid'])

    def test_import_reflection_dynamic_execution_files_and_timers_are_unavailable(self):
        for source in ('import os','from time import perf_counter','api.__class__',
                       '(1).__class__.__mro__','class X: pass','try:\n pass\nexcept:\n pass',
                       'async def f(): pass','__builtins__','lambda: 1','api.submit = print'):
            with self.subTest(source=source):self.assertFalse(check(source)['valid'])
        for source in ('open("/etc/passwd")','eval("1+1")','exec("pass")','getattr(api,"__dict__")',
                       'time()','perf_counter()','hash("x")','id(api)','globals()','random()','socket()'):
            with self.subTest(source=source):
                self.assertEqual(self.run_code(source)['error']['type'],'NameError')

    def test_big_native_operations_rejected_before_work_and_no_handle_representation(self):
        sources=('"x" * 1000000000','[0] * 1000000000','10 ** 1000000000',
                 '"%1000000000s" % "x"','"x".replace("", "a", -1) * 100000000',
                 'str([api])','str({"x": api.observe})','print([len])',
                 'a=[]\na.append(a)','a={}\na["a"]=a', '{}[api.observe]', '{api: 1}', '{}.pop(api)')
        for source in sources:
            with self.subTest(source=source):
                result=self.run_code(source)
                self.assertIsNotNone(result['error'])
                self.assertNotIn('0x',result['output'])
        self.assertEqual(self.run_code('print(1.5**2, 2**-2)')['output'],'2.25 0.25\n')

    def test_hash_seed_cannot_change_output_or_metering(self):
        code="from engine.scripting import Machine; import json; print(json.dumps(Machine(\"print({'秦': 1, '韩': 2})\",lambda *a:None).run(),sort_keys=True))"
        outputs=[]
        for seed in ('1','17'):
            result=subprocess.run([str(ROOT/'.venv/bin/python'),'-c',code],cwd=ROOT,
                env={**os.environ,'PYTHONHASHSEED':seed},text=True,capture_output=True,check=True)
            outputs.append(json.loads(result.stdout))
        self.assertEqual(outputs[0],outputs[1])

    def test_api_calls_and_output_have_independent_limits(self):
        machine=Machine('for i in range(30):\n    api.query("x")',lambda *a:0)
        result=machine.run()
        self.assertEqual(result['error']['message'],'API call budget exceeded')
        self.assertEqual(result['calls'],24)
        result=self.run_code('print("x" * 100)',output=32)
        self.assertEqual(result['error']['message'],'Output size limit exceeded')
        self.assertEqual(result['output'],'')

    def test_evaluation_order_short_circuit_loop_control_and_local_scope(self):
        source='''
x = 10
x += 2
values = []
for i in range(5):
    if i == 1:
        continue
    if i == 4:
        break
    values.append(i)
else:
    values.append(99)
def f(x=2):
    return x+1
print(values, f(), f(x=4), x, False and missing, True or missing)
'''
        result=self.run_code(source)
        self.assertIsNone(result['error'],result)
        self.assertEqual(result['output'],'[0, 2, 3] 3 5 12 False True\n')


if __name__=='__main__':unittest.main()
