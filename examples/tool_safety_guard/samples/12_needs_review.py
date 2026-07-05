import socket

host = input("host: ")
sock = socket.create_connection((host, 443), timeout=3)
print(sock.getpeername())
