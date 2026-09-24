import sys, unittest
sys.path.insert(0, ".")
import rl.jitter as jitter
import tests.test_rule_v10 as M

class Patched(M.TestJitterRegression):
    def tearDown(self):
        jitter.restore()
        print("  [tearDown] jitter.restore() 已调用")

s = unittest.TestSuite([
    unittest.defaultTestLoader.loadTestsFromTestCase(Patched),
    unittest.defaultTestLoader.loadTestsFromTestCase(M.TestNoMountainSpecialCase),
])
r = unittest.TextTestRunner(verbosity=1).run(s)
print("结果：", "OK" if r.wasSuccessful() else "FAILED")
print("MARKER-7731")
