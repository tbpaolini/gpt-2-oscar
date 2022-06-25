import socket, os


class IrcClient():

    def __init__(self, server:str, port:int, user:str, password:str, channel:str):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.channel = channel
        
        self.connect()
    
    def connect(self):
        self.sock.connect((self.server, self.port))
        self.sock.send(f"CAP REQ :twitch.tv/tags\n".encode(encoding="utf-8"))
        self.sock.send(f"PASS {self.password}\n".encode(encoding="utf-8"))
        self.sock.send(f"NICK {self.user}\n".encode(encoding="utf-8"))
        # self.sock.send(f"PRIVMSG #tiago_paolini :Olá! Sucesso :)\n".encode(encoding="utf-8"))
        pass
    
    def receive(self):
        while self.running:
            data = self.sock.recv(2048)
            print(data.decode(encoding="utf-8").split("\r\n"))


if __name__ == "__main__":

    IrcClient(
        server = "irc.chat.twitch.tv",
        port = 6667,
        user = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel = "tiago_paolini",
    )