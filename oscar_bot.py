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
        self.input_queue = mp.Queue()
        self.output_queue = mp.Queue()
        model_process = mp.Process(
            target=interact_model,
            kwargs= {"input_queue": self.input_queue, "output_queue": self.output_queue},
        )
        model_process.start()

        # Connecting to Twitch's IRC server
        print("Connecting to Twitch...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.channel = channel
        self.connect()

        input_thread = td.Thread(target=self.get_messages)
        input_thread.start()
        output_thread = td.Thread(target=self.ai_response)
        output_thread.start()

        # self.workers = (model_process, input_thread, output_thread)
        # exit_listener  = td.Thread(target=self.clean_exit)
        # exit_listener.start()
    
    def command(self, command:str):
        self.sock.send(f"{command}\n".encode(encoding="utf-8"))  # IMPORTANT: IRC commands must end with a newline character.
    
    def connect(self):
        self.sock.connect((self.server, self.port))
        self.command(f"CAP REQ :twitch.tv/tags")
        self.command(f"PASS {self.password}")
        self.command(f"NICK {self.user}")
        self.command(f"JOIN {self.channel}")

    def get_messages(self):
        while self.running:
            data = self.sock.recv(2048)
            for line in data.decode(encoding="utf-8").split("\r\n"):
                
                if line.startswith("PING "):
                    pong_msg = line.split()[1]
                    self.command(f"PONG {pong_msg}")
                
                text_match = MESSAGE_REGEX.search(line)
                
                if text_match is not None:
                    message_id, message_timestamp, message_body = text_match.groups()
                    message_timestamp = float(message_timestamp) / 1000.0
                    self.input_queue.put_nowait((message_body, message_id))

            # self.question = input()
            # if self.question in SHUTDOWN: break
            # self.input_queue.put_nowait((self.question, 0))
    
    def ai_response(self):
        while True:
            response = self.output_queue.get()
            if response == STOP: break
            message_body, message_id = response
            
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