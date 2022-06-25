from src.interactive_conditional_samples import interact_model, STOP
from bot.text_process import post_process, pre_process
import socket, ssl, os, re
import multiprocessing as mp
import threading as td
from time import sleep
from datetime import datetime, timedelta
from pathlib import Path
from random import randint

# Regular expression to get the message's username, ID, timestamp, and body
MESSAGE_REGEX = re.compile(r"(?i)^.+?;display-name=(\w+).+?;id=([\w-]+);.+?;tmi-sent-ts=([\d]+);.+? PRIVMSG #\w+? :(.+)")

class OscarBot():
    
    def __init__(
        self, server:str, port:int, user:str, password:str, channel:str,    # Login credentials
        chatlog:Path=Path("chatlog.txt"),   # Path to the log file
        min_wait:int=1800,  # Wait time (in seconds) for the bot replying without bein mentioned,
        max_wait:int=2400   # the bot randomly choses a value between the min and max wait times.
    ):
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
        self.plain_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ssl_context = ssl.create_default_context()
        self.ssl_sock = self.ssl_context.wrap_socket(self.plain_sock, server_hostname=server)
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

        # Thread that takes the command for quitting the bot
        self.workers = (model_process, input_thread, output_thread)
        exit_listener  = td.Thread(target=self.clean_exit)
        exit_listener.start()

        # Log to file the messages that the bot reply to
        self.chatlog = chatlog

        # Cooldown for the bot to reply to a message without being mentioned
        self.min_wait = min_wait
        self.max_wait = max_wait
        self.cooldown = timedelta(seconds=randint(self.min_wait, self.max_wait))
        
        # Keep track of when was the bot's last reply
        # (the value of datetime.min means that the bot has not replied yet)
        self.last_reply_time = datetime.min
    
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
        """Log in to the IRC server."""

        if not self.running: return
        
        retry_count = 0
        while True:
            try:
                self.ssl_sock.connect((self.server, self.port))     # Connect to the server
                self.command(f"CAP REQ :twitch.tv/tags")        # Request Tags on the messages (allows the bot to get the message's ID)
                self.command(f"PASS {self.password}")           # The OAuth token from Twitch
                self.command(f"NICK {self.user}")               # The username of the bot
                self.command(f"JOIN {self.channel}")            # The Twitch channel the bot is listening
                
                # Exit the function if no errors happened during connection
                return
            
            except (OSError, InterruptedError):
                # Retry after some time, if the connection failed
                # The wait time begins at 1 second, and doubles each retry until a maximum of 128 seconds.
                if not self.running: return
                wait_time = min(2**retry_count, 128)
                retry_count += 1
                sleep(wait_time)
                continue

    def get_messages(self):
        """Keep listening for messages until the program is closed."""

        while self.running:
            
            # Wait for data from the server
            try:
                data = self.ssl_sock.recv(2048)
            except (OSError, InterruptedError):
                self.connect()
                continue

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
                    
                    # Parse the message's contents
                    username, message_id, message_timestamp, message_body = text_match.groups()

                    # Check how long ago the bot has last replied
                    last_reply_age = datetime.utcnow() - self.last_reply_time

                    # Check if the last bot reply happened longer ago than the cooldown time,
                    # or if "oscar" appears anywhere in the message body.
                    # If neither of the conditions are true, the message is skipped.
                    if not ( (last_reply_age > self.cooldown) or ("oscar" in message_body.lower()) ):
                        continue
                    
                    # Queue the message to be answered by the bot
                    message_body = pre_process(message_body)
                    self.input_queue.put_nowait((message_body, message_id))

                    # Reset the cooldown time
                    self.last_reply_time = datetime.utcnow()
                    self.cooldown = timedelta(seconds=randint(self.min_wait, self.max_wait))

                    # Log the response
                    message_timestamp = float(message_timestamp) / 1000.0
                    log_msg = f"{datetime.fromtimestamp(message_timestamp)}: [{username}] {message_body}\n"
                    print(log_msg, end="")
                    with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                        chatlog_file.write(log_msg)
    
    def ai_response(self):
        """The AI responding the user's messages."""

        # Keep checking for new AI responses until the program is closed
        while True:
            response = self.output_queue.get()      # Wait for a new item at the output queue
            if response == STOP: break              # Exit if got the STOP signal
            message_body, message_id = response     # Get the response's contents and the ID of the message being replied to
            
            # Post the response to the chat
            message_body = post_process(message_body)
            self.command(f"@reply-parent-msg-id={message_id} PRIVMSG {self.channel} :{message_body}\n")

            # Log the response
            log_msg = f"{datetime.utcnow()}: [{self.user}] {message_body}\n"
            print(log_msg, end="")
            with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                chatlog_file.write(log_msg)
    
    def clean_exit(self, *args):
        """Allows the program to exit when 'stop', 'quit', or 'exit' is entered on the terminal;"""
        while True:
            user_input = input().strip().lower()
            if user_input in ("stop", "quit", "exit"):
                break
            else:
                print("To shutdown the bot, please type 'stop', 'quit', or 'exit' (without quotes) then press ENTER.")
        
        print("Closing bot...")
        self.input_queue.put_nowait(STOP)
        self.running = False
        self.ssl_sock.shutdown(socket.SHUT_RDWR)
        self.ssl_sock.close()
        for worker in self.workers:
            worker.join()

if __name__ == "__main__":
    OscarBot(
        server = "irc.chat.twitch.tv",
        port = 6697,
        user = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel = "#tiago_paolini",
    )