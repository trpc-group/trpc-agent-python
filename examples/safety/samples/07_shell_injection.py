"""Sample 07 — Shell injection via os.system and eval.

Expected decision: DENY
Triggers: PROC-002 (shell injection — os.system + eval)
"""

import os


def run_user_command(user_input: str):
    """Execute user-provided command through shell — extremely dangerous!"""
    os.system(f"echo {user_input} | bash")


def dynamic_eval(expression: str):
    """Evaluate arbitrary expressions — code injection risk!"""
    result = eval(expression)
    return result


if __name__ == "__main__":
    run_user_command("whoami")
    value = dynamic_eval("__import__('os').getcwd()")
    print(f"Result: {value}")
