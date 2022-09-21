from __future__ import annotations
from src.interactive_conditional_samples import interact_model, STOP
from bot.text_process import post_process, pre_process
from bot.filter import is_okay
import socket, ssl, os, re
import multiprocessing as mp
import threading as td
from time import sleep
from datetime import datetime, timedelta
from pathlib import Path
from random import randint, choice

# Google API (for interacting with YouTube)
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors

# Regular expression to get the message's username, ID, timestamp, and body
MESSAGE_REGEX = re.compile(r"(?i)^.+?;display-name=(\w+).+?;id=([\w-]+);.+?;tmi-sent-ts=([\d]+);.+? PRIVMSG #\w+? :(.+)")

# "Macros" for which platforms the bot is interacting with
TWITCH  = "twitch"
YOUTUBE = "youtube"

class OscarBot():
    
    def __init__(
        self, server:str, port:int, user:str, password:str, channel:str,    # Login credentials
        youtube_channel_id:str=None,    # ID of the YouTube channel where the bot will post (None to disable connection to YouTube)
        chatlog:Path=Path("chatlog.txt"),   # Path to the log file
        min_wait:int=1800,  # Wait time (in seconds) for the bot replying without bein mentioned,
        max_wait:int=2400,  # the bot randomly choses a value between the min and max wait times.
        streamavatars_wait_multiplier:int|float=2   # Multipliers to the above timers for the bot to interact through StreamAvatars (0 to disable)
    ):
        print("Starting OScar bot...")
        self.running = True
        self.workers = None

        # Connecting to YouTube
        self.youtube = None                         # YouTube API client for making requests
        self.youtube_channel = youtube_channel_id   # ID of the channel where the bot will be active
        self.youtube_chat_id = None                 # ID of the live chat of the YouTube stream (it changes every stream, so this ID is retrieved at runtime)
        self.chatlog_youtube = chatlog.with_stem(chatlog.stem + "-youtube")
        if self.youtube_channel is not None:
            self.connect_youtube()  # Authenticate on the YouTube API
            input_thread_youtube = td.Thread(target=self.get_youtube_messages)  # Listen for chat messages
            input_thread_youtube.start()
        else:
            input_thread_youtube = None
        
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
        self.auth_failed = False
        self.connect()

        # Separate threads for getting and sending messages
        # (because it is necessary to wait for input/output)
        input_thread_twitch = td.Thread(target=self.get_messages)
        input_thread_twitch.start()
        output_thread = td.Thread(target=self.ai_response)
        output_thread.start()

        # Thread that takes the command for quitting the bot
        exit_listener  = td.Thread(target=self.clean_exit)
        exit_listener.start()

        # Log to file the messages that the bot reply to
        self.chatlog = chatlog
        self.chatlog_blocked = self.chatlog.with_stem(chatlog.stem + "-blocked")

        # Cooldown for the bot to reply to a message without being mentioned
        self.min_wait = min_wait
        self.max_wait = max_wait
        self.cooldown = timedelta(seconds=randint(self.min_wait, self.max_wait))
        
        # Keep track of when was the bot's last reply
        # (the value of datetime.min means that the bot has not replied yet)
        self.last_reply_time = datetime.min

        # Interaction with the StreamAvatars' ferrets
        self.streamavatars_wait_multiplier = streamavatars_wait_multiplier
        self.duel = False   # If the bot is being challenged to a duel
        self.duel_last_user = "random"
        streamavatars_thread = td.Thread(target=self.streamavatars_interact)
        streamavatars_thread.start()
        
        # Ignore the duel messages of StreamAvatars
        self.ignored_messages = (
            f"Has Challenged @{self.user} To A Duel ",
            f"@{self.user} Has Challenged @",
            f"@{self.user} has accepted the duel against",
            f"Congratulations to @{self.user} for winning the duel!",
            f"@{self.user} Has Challenged",
            f"has accepted the duel against @{self.user}",
            f"@{self.user} has declined the duel",
            f"Could not find target {self.user}"
        )
        
        # The process and threads started by this script
        self.workers = (model_process, input_thread_twitch, input_thread_youtube, output_thread, streamavatars_thread)

        # Close the bot if authentication failed
        if self.auth_failed: self.close()
        exit_listener.join()
    
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
                
                # Check if the connection was successful, and print the server's response
                server_response = self.ssl_sock.recv(2048).decode(encoding="utf-8").split("\r\n")
                server_response += self.ssl_sock.recv(2048).decode(encoding="utf-8").split("\r\n")
                success = False
                for line in server_response:
                    if "Welcome, GLHF!" in line:
                        success=True
                    if "Login authentication failed" in line or "Improperly formatted auth" in line:
                        self.auth_failed = True
                    print(line)
                
                # A failed connection might be just a temporary issue, so we are not necessarily closing the bot
                if not success: print("Connection to Twitch failed.")

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
            
            except ValueError:
                # Create a new connection, if the old one failed reconnecting
                if not self.running: return
                self.ssl_sock.close()
                self.plain_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.ssl_context = ssl.create_default_context()
                self.ssl_sock = self.ssl_context.wrap_socket(self.plain_sock, server_hostname=self.server)
                wait_time = min(2**retry_count, 128)
                retry_count += 1
                sleep(wait_time)
                continue

    def connect_youtube(self):
        """Authenticate on YouTube through the Google API."""

        scopes = ["https://www.googleapis.com/auth/youtube.force-ssl"]
        api_service_name = "youtube"
        api_version = "v3"
        client_secrets_file = "google_client_secrets.json"

        # Get credentials and create an API client
        # (this is going to ask for the user to manually login on YouTube with the bot account)
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            client_secrets_file,
            scopes
        )
        credentials = flow.run_console()
        
        # YouTube API client for the bot
        self.youtube = googleapiclient.discovery.build(
            api_service_name,
            api_version,
            credentials=credentials
        )
    
    def get_messages(self):
        """Keep listening for messages until the program is closed."""

        empty_data = 0
        while self.running:
            
            # Wait for data from the server
            try:
                data = self.ssl_sock.recv(2048)
            except (OSError, InterruptedError):
                self.connect()
                if self.auth_failed: self.close()
                continue

            # If connection failed, the bot might keep receiving some empty packets
            if len(data) < 4:
                empty_data += 1
                if empty_data >= 5: self.connect()
            else:
                empty_data = 0

            # Decode the data's bytes into Unicode text and split its lines
            for line in data.decode(encoding="utf-8").split("\r\n"):
                
                # Respond the server's PING message with a corresponding PONG
                if line.startswith("PING "):
                    pong_msg = line.replace("PING", "PONG", 1)
                    self.command(pong_msg)
                    continue
                
                # Parse the message's content
                text_match = MESSAGE_REGEX.search(line)
                
                # Place the message on the queue to be answered
                if text_match is not None:
                    
                    # Parse the message's contents
                    username, message_id, message_timestamp, message_body = text_match.groups()

                    # Check if the bot was challenged to a duel
                    if ("!duel" in message_body) and (self.user.lower() in message_body.lower()):
                        self.duel = True
                        self.duel_last_user = username
                        continue

                    # Send a duel request back if StreamAvatars failed to find this bot
                    # (that might happen if the bot did not send a message in a while)
                    if (f"Could not find target {self.user}" == message_body) and (username.lower() == self.channel[1:].lower()):
                        self.duel = False
                        self.command(f"PRIVMSG {self.channel} :!duel {self.duel_last_user}")
                        continue

                    # Do not respond to the automatic duel messages
                    ignore = False
                    for ignored in self.ignored_messages:
                        if ignored in message_body:
                            ignore = True
                            break
                    if (ignore): continue

                    # Check how long ago the bot has last replied
                    last_reply_age = datetime.utcnow() - self.last_reply_time

                    # Check if the last bot reply happened longer ago than the cooldown time,
                    # or if "oscar" appears anywhere in the message body.
                    # If neither of the conditions are true, the message is skipped.
                    if not ( (last_reply_age > self.cooldown) or ("oscar" in message_body.lower()) ):
                        continue
                    
                    # Queue the message to be answered by the bot
                    message_body = pre_process(message_body)
                    self.input_queue.put_nowait((TWITCH, message_body, message_id))

                    # Reset the cooldown time
                    self.last_reply_time = datetime.utcnow()
                    self.cooldown = timedelta(seconds=randint(self.min_wait, self.max_wait))

                    # Log the response
                    message_timestamp = float(message_timestamp) / 1000.0
                    log_msg = f"{datetime.fromtimestamp(message_timestamp)}: [{username}] {message_body}\n"
                    print(log_msg, end="")
                    with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                        chatlog_file.write(log_msg)
                    
                    # Print on the terminal the time when the bot's cooldown expires
                    print(f"Next response: {self.last_reply_time + self.cooldown}", end="\r")
    
    def get_youtube_messages(self):
        """Keep listening for chat messages on YouTube until the program is closed"""

        # Whether the channel is currently streaming
        is_streaming = False
        
        # ID of the channel's live chat (this ID changes for each stream)
        self.youtube_chat_id = None

        # When to check if the channel is streaming
        next_check = datetime.utcnow()

        # Listen for chat messages if the channel is currently streaming
        while self.running:
            # Note: We are going to wait 20 minutes between checks for live streams because they
            #       use a lot of of quota points (100 points, from a daily limit of 10000 points).
            #       This happens because there isn't a straighfoward way on the YouTube API to
            #       check if someone else is streaming. We need to perform a search, then
            #       filter by live videos and the channel ID. And searches are an expensive
            #       operation in the YouTube API.
            wait_time = timedelta(minutes=20)
            
            # Check if the channel is currently streaming
            while self.running:
                
                # Is it the time to check if the channel is streaming?
                if datetime.utcnow() >= next_check:
                    
                    # Time for checking again if the channel is streaming
                    next_check = datetime.utcnow() + wait_time
                    
                    # Search for live videos of the channel (results sorted by date, descending)
                    search_request = self.youtube.search().list(
                        part="id",
                        channelId=self.youtube_channel,
                        eventType="live",
                        maxResults=1,
                        order="date",
                        type="video"
                    )
                    search_results = search_request.execute()
                    
                    # If there are any results, then the channel is streaming
                    if (search_results["pageInfo"]["totalResults"] > 0):
                        is_streaming = True
                        stream_id = search_results["items"][0]["id"]["videoId"]

                        # Get the ID of the stream's live chat
                        stream_id_request = self.youtube.videos().list(
                            part="liveStreamingDetails",
                            id=stream_id
                        )
                        stream_id_results = stream_id_request.execute()
                        self.youtube_chat_id = stream_id_results["items"][0]["liveStreamingDetails"]["activeLiveChatId"]

                        # Break from the loop if the channel is streaming
                        break
                
                # Wait one second before restarting the loop
                sleep(1.0)
            
            # Listening for chat messages
            next_reply_time = datetime.utcnow()
            
            # Retrieve the first batch of chat messages
            parsed_old_messages = False
            if is_streaming:
                messages_request = self.youtube.liveChatMessages().list(
                    liveChatId=self.youtube_chat_id,
                    part="snippet"
                )
                try:
                    messages_results = messages_request.execute()
                except googleapiclient.errors.HttpError:
                    # The request raises an error if the stream has ended
                    is_streaming = False

            # Dictionaries to associate the ID's of the messages with their respective contents and author
            chat_messages = {}  # Content of the messages
            chat_authors  = {}  # Authors of the messages
            
            # Keep retrieving the next messages
            while self.running and is_streaming:
                
                # Parse the previously retrieved messages
                # (associate the author's ID with their message)
                for message in messages_results["items"]:
                    try:
                        message_body = message["snippet"]["displayMessage"]
                    except KeyError:
                        # Skip the message if it has not a text body
                        # (that might be the case of event messages)
                        continue
                    author_id = message["snippet"]["authorChannelId"]
                    chat_messages[author_id] = message_body
                
                # Get the name of the author of each message
                authors_request = self.youtube.channels().list(
                    part="snippet",
                    id=",".join(author for author in chat_messages),
                    maxResults=len(chat_messages)
                )
                authors_results = authors_request.execute()

                # Process the received messages
                if "items" in authors_results:
                    # Associate the author's ID with their name
                    for author in authors_results["items"]:
                        chat_authors[author["id"]] = author["snippet"]["title"]
                    
                    # Log the message to file
                    for author_id, message_body in chat_messages.items():
                        with open(self.chatlog_youtube, "at", encoding="utf-8") as youtube_log:
                            youtube_log.write(f"{datetime.utcnow()}: [{chat_authors[author_id]}] {message_body}\n")
                    
                        # Check if the message needs to be replied by the bot
                        if (parsed_old_messages) and (datetime.utcnow() >= next_reply_time or "oscar" in message_body.lower()):
                            
                            # Reset the cooldown for the next bot's response
                            cooldown = randint(self.min_wait, self.max_wait)
                            next_reply_time += timedelta(seconds=cooldown)
                            
                            # Enqueue the message to be answered
                            message_body = pre_process(message_body)
                            self.input_queue.put_nowait((YOUTUBE, message_body, chat_authors[author_id]))

                            # Log the response
                            log_msg = f"{datetime.utcnow()}: [{chat_authors[author_id]}] {message_body}\n"
                            print(log_msg, end="")
                            with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                                chatlog_file.write(log_msg)
                            
                            # Print on the terminal the time when the bot's cooldown expires
                            print(f"Next response: {next_reply_time}", end="\r")
                
                # Clear the dictionaries for the next loop
                chat_messages.clear()
                chat_authors.clear()

                # Wait some time before retrieving the next messages
                # (the API's response tells how long to wait)
                sleep(messages_results["pollingIntervalMillis"] / 1000)
                
                # Retrieve the next batch of messages
                parsed_old_messages = True
                while True:
                    retry_count = 0
                    messages_request = self.youtube.liveChatMessages().list(
                        liveChatId=self.youtube_chat_id,
                        part="snippet",
                        pageToken=messages_results["nextPageToken"]
                    )
                    try:
                        messages_results = messages_request.execute()
                        break
                    except googleapiclient.errors.HttpError:
                        # The request raises an error if the stream has ended
                        is_streaming = False
                        break
                    except TimeoutError:
                        # Wait then retry if there was a timeout
                        retry_count += 1
                        if retry_count > 5: break
                        sleep(2 ** retry_count)
                    except KeyError:
                        # Begin retrieving the chat again if there was no "nextPageToken"
                        parsed_old_messages = False
                        messages_request = self.youtube.liveChatMessages().list(
                            liveChatId=self.youtube_chat_id,
                            part="snippet",
                        )
                        try:
                            messages_results = messages_request.execute()
                        except (googleapiclient.errors.HttpError, TimeoutError):
                            break
    
    def post_on_youtube_chat(self, message:str):
        """Post a message on the YouTube chat"""

        retry_count = 0
        
        while True:
            bot_response = self.youtube.liveChatMessages().insert(
                part="snippet",
                body={
                    "snippet": {
                        "type": "textMessageEvent",
                        "liveChatId": self.youtube_chat_id,
                        "textMessageDetails": {
                        "messageText": message
                        }
                    }
                }
            )
            try:
                bot_response.execute()
            except (googleapiclient.errors.HttpError, TimeoutError):
                retry_count += 1
                if retry_count > 5: return
                sleep(2 ** retry_count)
            return
    
    def ai_response(self):
        """The AI responding the user's messages."""

        # Keep checking for new AI responses until the program is closed
        while self.running:
            response = self.output_queue.get()      # Wait for a new item at the output queue
            if response == STOP: break              # Exit if got the STOP signal
            platform, message_body, message_id = response   # Get the response's contents and the ID of the message being replied to

            # Check if the response do not have any blocked words
            message_body = post_process(message_body)
            if not is_okay(message_body):
                # Log the blocked message
                with open(self.chatlog_blocked, "at", encoding="utf-8") as file:
                    file.write(f"{datetime.utcnow()}: [{self.user}] {message_body}\n")
                
                # Replace the message with something funny, instead of saying something potentially offensive
                message_body = "I can't say what I just thought gopiraSmug"
            
            # Post the response to the chat
            if platform == TWITCH:
                self.command(f"@reply-parent-msg-id={message_id} PRIVMSG {self.channel} :{message_body}")
            elif platform == YOUTUBE:
                self.post_on_youtube_chat(f"@{message_id} {message_body}")

            # Log the response
            log_msg = f"{datetime.utcnow()}: [{self.user}] {message_body}\n"
            print(log_msg, end="")
            with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                chatlog_file.write(log_msg)
            
            # Print on the terminal the time when the bot's cooldown expires
            print(f"Next response: {self.last_reply_time + self.cooldown}", end="\r")
    
    def streamavatars_interact(self):
        """Every now and then, send some random StreamAvatars commands to interact with other users."""
        
        if (self.streamavatars_wait_multiplier <= 0): return
        
        # Minimum and maximum wait times between the StreamAvatars commands
        sv_min_wait = int(self.min_wait * self.streamavatars_wait_multiplier)
        sv_max_wait = int(self.max_wait * self.streamavatars_wait_multiplier)

        # Safeguard so the bot does not accidentally gets set up to a very low cooldown time
        if (sv_min_wait < 300): return
        
        # Commands the bot can use
        sv_commands = (
            "!duel random",
            "!attack random",
            "!hug random"
        )
        
        # Time to wait between commands (chosen randomly between the min and max wait times)
        sv_command_cooldown = timedelta(seconds=randint(sv_min_wait, sv_max_wait))
        sv_last_command = datetime.utcnow()

        # Safeguard for the bot to not spam "!accept" messages
        sv_last_duel_time = datetime.min
        sv_duel_cooldown = timedelta(seconds=5.0)

        # Send the commands after the cooldown time has elapsed
        while self.running:
            
            # The bot only sends commands if the chat is active
            if (datetime.utcnow() - self.last_reply_time) > self.cooldown:
                sleep(5.0)
                continue
            
            # Random commands (hug, attack, duel)
            sv_last_command_age = datetime.utcnow() - sv_last_command
            if (sv_last_command_age > sv_command_cooldown):
                sv_command = choice(sv_commands)
                self.command(f"PRIVMSG {self.channel} :{sv_command}")
                sv_command_cooldown = timedelta(seconds=randint(sv_min_wait, sv_max_wait))
                sv_last_command = datetime.utcnow()
            
            # Accept an incoming duel
            if self.duel:
                self.duel = False
                last_duel_age = datetime.utcnow() - sv_last_duel_time
                if (last_duel_age > sv_duel_cooldown):
                    sleep(1.5)  # Give the StreamAvatars some time to process the duel request on its side
                    self.command(f"PRIVMSG {self.channel} :!accept")
                    sv_last_duel_time = datetime.utcnow()

            # Wait a second before checking again for more commands
            sleep(1.0)
    
    def clean_exit(self, *args):
        """Allows the program to exit when 'stop', 'quit', or 'exit' is entered on the terminal;"""
        while self.running:
            user_input = input().strip().lower()
            if user_input in ("stop", "quit", "exit"):
                self.close()    # Close the connection and cleanly close the program
                return
            elif self.running:
                print("To shutdown the bot, please type 'stop', 'quit', or 'exit' (without quotes) then press ENTER.")
        
    def close(self):
        print("Shutting down bot...")
        
        # Close the connection
        self.input_queue.put_nowait(STOP)
        self.output_queue.put_nowait(STOP)
        self.running = False
        if not self.auth_failed: self.command("QUIT")
        self.ssl_sock.shutdown(socket.SHUT_RDWR)
        self.ssl_sock.close()

        # Wait until all workers have started (for the case we are still during startup)
        while self.workers is None: sleep(0.1)
        
        # Give some time to the process to end by itself
        sleep(1.0)

        # Terminate the process if it is still running
        if self.workers[0].is_alive(): self.workers[0].terminate()
        
        # Join the processes and threads 
        for worker in self.workers:
            if worker is not None: worker.join()
        
        # This "error message" is here because the clean_exit() thread might block the exit while waiting for input
        # Pressing ENTER give it an input so it no longer blocks
        if self.auth_failed:
            print("Login credentials are wrong.\nPress ENTER exit...")

if __name__ == "__main__":
    OscarBot(
        server = "irc.chat.twitch.tv",
        port = 6697,
        user = "oscar__bot",
        password = os.getenv("TWITCH_KEY"),
        channel = "#piratesoftware",
        youtube_channel_id = "UCMnULQ6F6kLDAHxofDWIbrw"
    )