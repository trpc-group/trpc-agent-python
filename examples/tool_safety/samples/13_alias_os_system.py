# Sample 13: import alias process spawn.
# import os as x; x.system(...) must be DENIED.
import os as x
x.system("rm -rf /tmp/evil")
