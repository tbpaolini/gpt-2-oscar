from src.interactive_conditional_samples import interact_model, STOP
from bot.text_process import post_process
import socket, os, re
import multiprocessing as mp
import threading as td
from time import sleep

# Regular expression to get the message's ID, timestamp, and body
MESSAGE_REGEX = re.compile(r"(?i)^.+?;id=([\w-]+);.+?;tmi-sent-ts=([\w-]+);.+? PRIVMSG #\w+? :(.+)")

SHUTDOWN = ("stop", "quit", "exit")

class OscarBot():
    
    def __init__(self, server:str, port:int, user:str, password:str, channel:str):
        print("Starting OScar bot...")
        self.running = True
        
        # Starting the AI model
        self.input_queue = mp.Queue()   # The messages the bot need to answer
        self.output_queue = mp.Queue()  # The responses the bot gave
        model_process = mp.Process(
            target=interact_model,
            kwargs= {"input_queue": self.input_queue, "output_queue": self.output_queue},
        )
        model_process.start()   # The AI model is being run in a separate process because it uses lots of CPU

        # Connecting to Twitch's IRC server
        print("Connecting to Twitch...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.channel = channel
        self.connect()

        # Separate threads for getting and sending messages
        # (because it is necessary to wait for input/output)
        input_thread = td.Thread(target=self.get_messages)
        input_thread.start()
        output_thread = td.Thread(target=self.ai_response)
        output_thread.start()

        # self.workers = (model_process, input_thread, output_thread)
        # exit_listener  = td.Thread(target=self.clean_exit)
        # exit_listener.start()
    
    def command(self, command:str):
        """Sends a raw command to the IRC server."""
        self.sock.send(f"{command}\n".encode(encoding="utf-8"))  # IMPORTANT: IRC commands must end with a newline character.
    
    def connect(self):
        """Log in to the IRC server."""
        self.sock.connect((self.server, self.port))     # Connect to the server
        self.command(f"CAP REQ :twitch.tv/tags")        # Request Tags on the messages (allows the bot to get the message's ID)
        self.command(f"PASS {self.password}")           # The OAuth token from Twitch
        self.command(f"NICK {self.user}")               # The username of the bot
        self.command(f"JOIN {self.channel}")            # The Twitch channel the bot is listening

    def get_messages(self):
        """Keep listening for messages until the program is closed."""

        while self.running:
            
            # Wait for data from the server
            data = self.sock.recv(2048)

            # Decode the data's bytes into Unicode text and split its lines
            for line in data.decode(encoding="utf-8").split("\r\n"):
                
                # Respond the server's PING message with a corresponding PONG
                if line.startswith("PING "):
                    pong_msg = line.split()[1]
                    self.command(f"PONG {pong_msg}")
                
                # Parse the message's content
                text_match = MESSAGE_REGEX.search(line)
                
                # Place the message on the queue to be answered
                if text_match is not None:
                    message_id, message_timestamp, message_body = text_match.groups()
                    message_timestamp = float(message_timestamp) / 1000.0
                    self.input_queue.put_nowait((message_body, message_id))

            # self.question = input()
            # if self.question in SHUTDOWN: break
            # self.input_queue.put_nowait((self.question, 0))
    
    def ai_response(self):
        """The AI responding the user's messages."""

        # Keep checking for new AI responses until the program is closed
        while True:
            response = self.output_queue.get()      # Wait for a new item at the output queue
            if response == STOP: break              # Exit if got the STOP signal
            message_body, message_id = response     # Get the response's contents and the ID of the message being replied to
            
            # Post the response to the chat
            self.command(f"@reply-parent-msg-id={message_id} PRIVMSG {self.channel} :{post_process(message_body)}\n")
    
    def clean_exit(self, *args):
        while True:
            if self.question.lower() in SHUTDOWN: break
            sleep(1)
        
        print("Closing bot...")
        self.input_queue.put_nowait(STOP)
        self.running = False
        for worker in self.workers:
            worker.join()

if __name__ == "__main__":
    OscarBot(
        server = "irc.chat.twitch.tv",
        port = 6667,
        user = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel = "#tiago_paolini",
    )