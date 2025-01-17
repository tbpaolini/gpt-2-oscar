import socket, os, re
import multiprocessing as mp

# Regular expression to get the message's ID, timestamp, and body
MESSAGE_REGEX = re.compile(r"(?i)^.+?;id=([\w-]+);.+?;tmi-sent-ts=([\w-]+);.+? PRIVMSG #\w+? :(.+)")

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
        self.run()
    
    def send(self, command:str):
        self.sock.send(f"{command}\n".encode(encoding="utf-8"))  # IMPORTANT: IRC commands must end with a newline character.
    
    def connect(self):
        self.sock.connect((self.server, self.port))
        self.send(f"CAP REQ :twitch.tv/tags")
        self.send(f"PASS {self.password}")
        self.send(f"NICK {self.user}")
        self.send(f"JOIN {self.channel}")
        pass
    
    def run(self):
        while self.running:
            data = self.sock.recv(2048)
            for line in data.decode(encoding="utf-8").split("\r\n"):
                
                if line.startswith("PING "):
                    pong_msg = line.split()[1]
                    self.send(f"PONG {pong_msg}")
                
                text_match = MESSAGE_REGEX.search(line)
                
                if text_match is not None:
                    message_id, message_timestamp, message_body = text_match.groups()
                    message_timestamp = float(message_timestamp) / 1000.0

                print(line)


if __name__ == "__main__":

    IrcClient(
        server = "irc.chat.twitch.tv",
        port = 6667,
        user = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel = "#tiago_paolini",
    )