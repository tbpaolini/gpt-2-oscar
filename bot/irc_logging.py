from __future__ import annotations
import os, socket, ssl
from pathlib import Path
from time import sleep
from datetime import datetime

class IRCLogger():
    """ Log to file all the messages raw messages received from the IRC server.
    This is for the purpose of improving the bot and dealing with potential issues.
    
    Possibly in the future this data is going to be used to train a model for the bot
    to decide which messages to respond or not.
    """

    def __init__(
        self, server:str, port:int, user:str, password:str, channel:str,
        chatlog:Path|str = "irc_log.txt"
    ):
        self.server   = server
        self.port     = port
        self.user     = user
        self.password = password
        self.channel  = channel
        self.chatlog  = Path(chatlog)

        self.ssl_context:ssl.SSLContext = None
        self.plain_sock:socket.socket   = None
        self.ssl_sock:ssl.SSLSocket     = None

        try:
            self.connect()
            print("Logging has started...")
            self.receive()
        except KeyboardInterrupt:
            self.command("QUIT")
            print("Logging finished")
            pass
    
    def command(self, command:str):
        """Sends a raw command to the IRC server."""
        
        retry_count = 0
        while retry_count < 5:
            try:
                # Try up to 5 times to send the command
                self.ssl_sock.send(f"{command}\n".encode(encoding="utf-8"))  # IMPORTANT: IRC commands must end with a newline character.
                return
            
            except (OSError, InterruptedError):
                # Attempt reconnecting upon failue
                self.connect()
                retry_count += 1
    
    def connect(self):
        
        retry_count = 0

        while True:

            try:
                # Close the existing socket
                if self.ssl_sock is not None:
                    self.ssl_sock.close()

                if self.plain_sock is not None:
                    self.plain_sock.close()
                
                # Create a new socket
                self.plain_sock  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.ssl_context = ssl.create_default_context()
                self.ssl_sock    = self.ssl_context.wrap_socket(self.plain_sock, server_hostname=self.server)

                # Connect and authenticate to the server
                self.ssl_sock.connect((self.server, self.port))     # Connect to the server
                self.command(f"CAP REQ :twitch.tv/commands twitch.tv/membership twitch.tv/tags")  # Request all capabilities to get all messages
                self.command(f"PASS {self.password}")           # The OAuth token from Twitch
                self.command(f"NICK {self.user}")               # The username of the bot
                self.command(f"JOIN {self.channel}")            # The Twitch channel the bot is listening

                # Exit from the loop on connection success
                break
            
            except (OSError, InterruptedError):
                # If the connection failed, wait before retrying
                # The wait time starts at 1 second and it keeps doubling until a cap of 300 seconds
                wait_time = min(2**retry_count, 300)
                sleep(wait_time)
                retry_count += 1
    
    def receive(self):
        
        empty_count = 0
        
        while True:
            try:
                # Wait for data from the server
                data = self.ssl_sock.recv(2048)
            except (OSError, InterruptedError):
                # Reconnect if the connection has failed
                self.connect()
                continue

            # Decode the data's bytes into Unicode text and split its lines
            for line in data.decode(encoding="utf-8", errors="replace").split("\r\n"):
                
                # Skip the line if it has no text
                if not line.strip():
                    # Reconnect if received too many empty data
                    empty_count += 1
                    if (empty_count >= 5): self.connect()
                    continue
                else:
                    empty_count = 0
                
                # Respond the server's PING message with a corresponding PONG
                if line.startswith("PING "):
                    pong_msg = line.replace("PING", "PONG", 1)
                    self.command(pong_msg)
                    # Do not log the PING message
                    continue
                
                # Append the raw IRC message to the log file
                with open(self.chatlog, "at") as file:
                    file.write(f"{datetime.utcnow()} | {line}\n")
                
                # Reconnect the server needs to terminate the connections
                if line == ":tmi.twitch.tv RECONNECT": self.connect()


if __name__ == "__main__":
    IRCLogger(
        server   = "irc.chat.twitch.tv",
        port     = 6697,
        user     = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel  = "#piratesoftware",
    )
