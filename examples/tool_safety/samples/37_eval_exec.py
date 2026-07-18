# Sample 37: eval/exec dynamic code (deny).
code = "print(1)"
eval(code)
exec("import os")
