with open("/etc/sudoers", "w", encoding="utf-8") as stream:
    stream.write("unsafe")
