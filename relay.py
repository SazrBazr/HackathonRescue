import socket
listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
listen.bind(("0.0.0.0", 5007))
fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
print("Relaying 5007 -> WSL 5006 ...")
while True:
    data, _ = listen.recvfrom(1024)
    fwd.sendto(data, ("172.19.11.200", 5006))
    print("relayed", data)
