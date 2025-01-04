from __future__ import annotations

# src.interactive_conditional_samples uses the local GPT-2 model (outdated, slower).
# bot.kindroid uses the remote Kindroid AI (much better).
# Only one of them should be imported, uncomment or comment the lines below as needed:
# from src.interactive_conditional_samples import interact_model, STOP
from bot.kindroid import interact_model, STOP

from bot.text_process import post_process, pre_process
from bot.filter import is_okay
import socket, ssl, os, re, pickle
import multiprocessing as mp
import threading as td
from time import sleep
from datetime import datetime, timedelta
from pathlib import Path
from random import randint, choice
from pprint import pprint
from traceback import print_exc

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
        self.youtube_chat_get = None                # YouTube API client for receiving chat messages
        self.youtube_chat_send = None               # YouTube API client for posting on the chat
        self.youtube_live_check = None              # YouTube API client for checking if the stream is live
        self.youtube_channel = youtube_channel_id   # ID of the channel where the bot will be active
        self.youtube_chat_id = None                 # ID of the live chat of the YouTube stream (it changes every stream, so this ID is retrieved at runtime)
        self.my_youtube_id = None                   # YouTube User ID of the bot (the ID will be retrieved at runtime)
        self.youtube_lock = td.Lock()               # Lock for thread synchronization because the Google API module is not thread safe
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
        self.channel = channel.lower() if channel.startswith("#") else f"#{channel.lower()}"
        self.auth_failed = False
        self.connect_twitch()

        # Separate threads for getting and sending messages
        # (because it is necessary to wait for input/output)
        input_thread_twitch = td.Thread(target=self.get_twitch_messages)
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
        self.next_youtube_reply = datetime.min

        # Interaction with the StreamAvatars' ferrets
        self.streamavatars_wait_multiplier = streamavatars_wait_multiplier
        self.duel = False   # If the bot is being challenged to a duel
        self.duel_last_user = "random"
        streamavatars_thread = td.Thread(target=self.streamavatars_interact)
        streamavatars_thread.start()
        
        # Ignore the duel messages of StreamAvatars
        self.ignored_messages = (
            "Has Challenged",
            "has accepted the duel against",
            "for winning the duel!",
            "has declined the duel",
            "Could not find target"
        )
        
        # The process and threads started by this script
        self.workers = (model_process, input_thread_twitch, input_thread_youtube, output_thread, streamavatars_thread)

        # Close the bot if authentication failed
        if self.auth_failed: self.close()
        exit_listener.join()
    
    def twitch_command(self, command:str):
        """Sends a raw command to the IRC server."""
        
        retry_count = 0
        while retry_count < 5:
            try:
                # Try up to 5 times to send the command
                self.ssl_sock.send(f"{command}\n".encode(encoding="utf-8"))  # IMPORTANT: IRC commands must end with a newline character.
                return
            
            except (OSError, InterruptedError):
                # Attempt reconnecting upon failue
                self.connect_twitch()
                retry_count += 1
    
    def connect_twitch(self):
        """Log in to the IRC server."""

        if not self.running: return
        
        retry_count = 0
        while True:
            try:
                self.ssl_sock.connect((self.server, self.port))     # Connect to the server
                self.twitch_command(f"CAP REQ :twitch.tv/tags")        # Request Tags on the messages (allows the bot to get the message's ID)
                self.twitch_command(f"PASS {self.password}")           # The OAuth token from Twitch
                self.twitch_command(f"NICK {self.user}")               # The username of the bot
                self.twitch_command(f"JOIN {self.channel}")            # The Twitch channel the bot is listening
                
                # Check if the connection was successful, and print the server's response
                server_response = self.ssl_sock.recv(2048).decode(encoding="utf-8", errors="replace").split("\r\n")
                server_response += self.ssl_sock.recv(2048).decode(encoding="utf-8", errors="replace").split("\r\n")
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

        # Note: Since the daily quota of the YouTube API is too low to deal with
        # live content, we are going to use 3 different API keys to get 3 quotas.
        # One for checking if the stream is live, one for receiving chat messages,
        # and another for sending chat messages.
        #
        # The API quota is 10000 points (daily). The usage is as follows:
        #   - Checking if the channel is live: 100 points
        #   - Checking for new chat messages: 5 points
        #   - Checking for the usernames of the messages: 1 point (can request multiple usernames at once)
        #   - Sending a chat message: 20 points
        
        # Set up the Google API parameters for using the YouTube Data API (version 3)
        scopes = ["https://www.googleapis.com/auth/youtube.force-ssl"]
        api_service_name = "youtube"
        api_version = "v3"
        
        # Authenticate for receiving chat messages
        api_key_1 = os.getenv("YOUTUBE_KEY_1")
        self.youtube_chat_get = googleapiclient.discovery.build(api_service_name, api_version, developerKey = api_key_1)
        
        # Authenticate for checking if the stream is live
        api_key_2 = os.getenv("YOUTUBE_KEY_2")
        self.youtube_live_check = googleapiclient.discovery.build(api_service_name, api_version, developerKey = api_key_2)
        
        # Authenticate for posting chat messages
        # (since it requires to login with a specific user, here it will open a prompt
        #  on the terminal that asks the user to visit a Google URL to login)
        self._saved_credentials = Path("auth.bin")
        if not self._saved_credentials.exists():
            client_secrets_file = "google_client_secrets.json"

            # Get credentials and create an API client
            # (this is going to ask for the user to manually login on YouTube with the bot account)
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                client_secrets_file,
                scopes
            )
            credentials = flow.run_console()
            
            # YouTube API client for the bot
            self.youtube_chat_send = googleapiclient.discovery.build(
                api_service_name,
                api_version,
                credentials=credentials
            )

            # Cache the login info so it does not need to be entered again on bot's restart
            self.cache_youtube_credentials()
        
        else:
            # Load the credentials from file, if that file exists
            with open(self._saved_credentials, "rb") as file:
                self.youtube_chat_send = pickle.load(file)
        
        # File where it will be logged the raw responses received from the YouTube API
        self._raw_youtube_log = Path("bot/yt_log.txt")

        # File where to log the errors raised by the YouTube API
        self._youtube_error_log = Path("bot/yt_error.txt")

        # Get the YouTube user ID of the bot
        request = self.youtube_chat_send.channels().list(
            part="id",
            mine=True
        )
        with self.youtube_lock:
            response = request.execute()
        self.my_youtube_id = response["items"][0]["id"]
        self.raw_youtube_log(response)

        print("Connected to YouTube.")
    
    def cache_youtube_credentials(self):
        """Save to file the YouTube credentials."""
        
        # Note: This is here for the case the bot needs to be restarted,
        # so it is not necessary to do the whole login process every time.
        # I am not sure for how long the file will be valid, I might need
        # to rework this part once I know. For now deleting the "auth.bin"
        # should be enough to force a new login.
        with open(self._saved_credentials, "wb") as file:
            pickle.dump(self.youtube_chat_send, file)
    
    def raw_youtube_log(self, response:dict):
        """Log the responses from the YouTube API."""
        
        with open(self._raw_youtube_log, "at", encoding="utf-8") as file:
            file.write(f"\n{datetime.utcnow()}\n")
            pprint(response, stream=file)
    
    def youtube_error_log(self):
        """Log the errors raised when making requests to the YouTube API."""

        with open(self._youtube_error_log, "at", encoding="utf-8") as error_log:
            error_log.write(f"{datetime.utcnow()}\n\n")
            print_exc(file=error_log)
            error_log.write("\n\n---------------\n")
    
    def get_twitch_messages(self):
        """Keep listening for messages until the program is closed."""

        empty_data = 0
        self._twitch_last_seen_user = "random"  # Remember the last user (for StreamAvatars' interactions)
        while self.running:
            
            # Wait for data from the server
            try:
                data = self.ssl_sock.recv(2048)
            except (OSError, InterruptedError):
                self.connect_twitch()
                if self.auth_failed: self.close()
                continue

            # If connection failed, the bot might keep receiving some empty packets
            if len(data) < 4:
                empty_data += 1
                if empty_data >= 5: self.connect_twitch()
            else:
                empty_data = 0

            # Decode the data's bytes into Unicode text and split its lines
            for line in data.decode(encoding="utf-8", errors="replace").split("\r\n"):
                
                # Respond the server's PING message with a corresponding PONG
                if line.startswith("PING "):
                    pong_msg = line.replace("PING", "PONG", 1)
                    self.twitch_command(pong_msg)
                    continue
                
                # Parse the message's content
                text_match = MESSAGE_REGEX.search(line)
                
                # Place the message on the queue to be answered
                if text_match is not None:
                    
                    # Parse the message's contents
                    username, message_id, message_timestamp, message_body = text_match.groups()
                    self._twitch_last_seen_user = username

                    # Check if the bot was challenged to a duel
                    if ("!duel" in message_body) and (self.user.lower() in message_body.lower()):
                        self.duel = TWITCH
                        self.duel_last_user = username
                        continue

                    # Check for duel messages from StreamAvatars
                    if username.lower() == self.channel[1:]:
                    
                        # Send a duel request back if StreamAvatars failed to find this bot
                        # (that might happen if the bot did not send a message in a while)
                        if (f"Could not find target {self.user}" == message_body):
                            self.duel = False
                            self.twitch_command(f"PRIVMSG {self.channel} :!duel {self.duel_last_user}")
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
                    self.input_queue.put_nowait((TWITCH, message_body, message_id, username))

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

        # Scheduled streams of the channel
        scheduled_streams:dict[str,datetime] = {}

        # When to check if the channel is streaming
        next_check = datetime.utcnow()

        # Regular expressions for checking incoming duels
        DUEL_REGEX = re.compile(r"(?i)^ *!duel +@{0,1}oscar")
        FAIL_REGEX = re.compile(r"(?i)Could not find target @{0,1}OScar")

        # The last seen user (to be used with StreamAvatars' interactions)
        self._youtube_last_seen_user = "random"

        # This value increments by 1 on each YouTube search, from 0 to 40, then it starts again.
        # When it is 40, we are going to check if there is a live stream scheduled.
        # Otherwise, we are going to check if there is a live stream currently ongoing.
        _cycle_count = 0

        # Listen for chat messages if the channel is currently streaming
        while self.running:
            # Note: We are going to wait 15 to 20 minutes between checks for live streams because they
            #       use a lot of of quota points (100 points, from a daily limit of 10000 points).
            #       This happens because there isn't a straighfoward way on the YouTube API to
            #       check if someone else is streaming. We need to perform a search, then
            #       filter by live videos and the channel ID. And searches are an expensive
            #       operation in the YouTube API.
            
            # Check if the channel is currently streaming
            while self.running:

                # Current time (UTC)
                now = datetime.utcnow()

                # Check if a scheduled stream is about to start
                for chat_id, start_time in scheduled_streams.items():
                    # Move to the next item on the dictionary if we are not yet at the stream's time
                    if start_time > now: continue
                    
                    # Flag the stream as ongoing if we are at the time, and get its Chat ID
                    is_streaming = True
                    self.youtube_chat_id = chat_id
                    break
                
                # Exit the loop if a scheduled stream is about to start
                if is_streaming:
                    del scheduled_streams[self.youtube_chat_id]     # Remove the scheduled stream from the dictionary
                    break
                
                # Is it the time to check if the channel is streaming?
                if now >= next_check:
                    
                    # Time for checking again if the channel is streaming
                    wait_time = timedelta(seconds=randint(900, 1200))
                    next_check = now + wait_time

                    # Whether to check for upcoming or ongoing streams
                    # (we are going to do this check roughly twice a day)
                    _current_event = "upcoming" if (_cycle_count == 40) else "live"
                    _cycle_count += 1
                    _cycle_count %= 41
                    
                    # Search for live videos of the channel (results sorted by date, descending)
                    search_request = self.youtube_live_check.search().list(
                        part="id,snippet",
                        channelId=self.youtube_channel,
                        eventType=_current_event,
                        maxResults=10,
                        order="date",
                        type="video"
                    )
                    with self.youtube_lock:
                        try:
                            search_results = search_request.execute()
                        except (BrokenPipeError, socket.timeout, ConnectionResetError):
                            # This is an error that for whatever reason happens on Linux, once in a while, with the Google API library.
                            # It does not seem to be fault of my code, and we can connect if we try again.
                            self.youtube_error_log()
                    
                    # If there are any results, then the channel is streaming
                    if (search_results["pageInfo"]["totalResults"] > 0):
                        
                        # Log the raw search results
                        self.raw_youtube_log(search_results)
                        
                        for video in search_results["items"]:
                            #Get the video's title
                            video_title:str = video["snippet"]["title"].lower()

                            # Ignore ferret streams
                            if "ferret" in video_title: continue
                            
                            # Get the Stream ID
                            stream_id = video["id"]["videoId"]

                            # Get the ID of the stream's live chat
                            stream_id_request = self.youtube_live_check.videos().list(
                                part="liveStreamingDetails",
                                id=stream_id
                            )
                            with self.youtube_lock:
                                try:
                                    stream_id_results = stream_id_request.execute()
                                except (BrokenPipeError, socket.timeout, ConnectionResetError):
                                    self.youtube_error_log()
                            self.raw_youtube_log(stream_id_results)
                            chat_id = stream_id_results["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
                            
                            if _current_event == "live":
                                # Set the streaming flag to True
                                is_streaming = True
                                self.youtube_chat_id = chat_id
                            
                            elif _current_event == "upcoming":
                                # Get the stream's starting time
                                start_time = datetime.fromisoformat(
                                    stream_id_results["items"][0]["liveStreamingDetails"]["scheduledStartTime"][:-1]
                                    # Note: The API returns the UTC time as an string that ends in "Z".
                                    #       The [:-1] slice is for skipping the Z at the end, so Python can convert it
                                    #       to a datetime object without raising an error.
                                )
                                
                                # Add the scheduled stream to the dictionary
                                # Note: The bot gets to the stream's chat 5 minutes before the scheduled start time
                                scheduled_streams[chat_id] = start_time - timedelta(minutes=5)

                        # Break from the loop if the channel is streaming
                        if is_streaming: break
                
                # Wait five seconds before restarting the loop
                sleep(5.0)
            
            # Listening for chat messages
            self.next_youtube_reply = datetime.utcnow()
            
            # Retrieve the first batch of chat messages
            parsed_old_messages = False
            if is_streaming:
                messages_request = self.youtube_chat_get.liveChatMessages().list(
                    liveChatId=self.youtube_chat_id,
                    part="snippet,authorDetails"
                )
                try:
                    with self.youtube_lock:
                        messages_results = messages_request.execute()
                except googleapiclient.errors.HttpError:
                    # The request raises an error if the stream has ended
                    is_streaming = False
                    self.youtube_error_log()
                except (BrokenPipeError, ConnectionResetError):
                    self.youtube_error_log()

            # Keep retrieving the next messages
            while self.running and is_streaming:
                
                # Log the raw messages to file (if there are any)
                if messages_results["items"]:
                    self.raw_youtube_log(messages_results)

                # Process the received chat messages
                for message in messages_results["items"]:
                    
                    # Get the message's text
                    try:
                        message_body = message["snippet"]["displayMessage"]
                    except KeyError:
                        # Skip the message if it has not a text body
                        # (that might be the case of event messages)
                        continue
                    
                    # Get the author's ID and username
                    author_id = message["snippet"]["authorChannelId"]
                    author_name = message["authorDetails"]["displayName"]
                    self._youtube_last_seen_user = author_name

                    # Get the message's date and time
                    message_datetime = message["snippet"]["publishedAt"]
                    
                    # Log the message to file
                    with open(self.chatlog_youtube, "at", encoding="utf-8") as youtube_log:
                        youtube_log.write(f"{message_datetime}: [{author_name}] {message_body}\n")
                    
                    # Handle incoming duels
                    if DUEL_REGEX.match(message_body) is not None:
                        self.duel = YOUTUBE
                        self.duel_last_user = author_name
                        continue

                    # Check the duel messages from StreamAvatars
                    if author_id == self.youtube_channel:
                        
                        # Resend a failed duel
                        if FAIL_REGEX.match(message_body) is not None:
                            self.duel = False
                            self.post_on_youtube_chat(f"!duel {self.duel_last_user}")
                            continue
                        
                        # Ignore the status messages
                        ignore = False
                        for ignored in self.ignored_messages:
                            if ignored in message_body:
                                ignore = True
                                break
                        if (ignore): continue
                
                    # Check if the message needs to be replied by the bot
                    # Note: The bot does not respond to messages posted before it went online.
                    #       It also ignores its own messages.
                    if (parsed_old_messages) \
                        and (datetime.utcnow() >= self.next_youtube_reply or "oscar" in message_body.lower()) \
                        and (author_id != self.my_youtube_id):
                        
                        # Reset the cooldown for the next bot's response
                        cooldown = randint(self.min_wait, self.max_wait)
                        self.next_youtube_reply = datetime.utcnow() + timedelta(seconds=cooldown)
                        
                        # Enqueue the message to be answered
                        message_body = pre_process(message_body)
                        self.input_queue.put_nowait((YOUTUBE, message_body, author_name, author_name))

                        # Log the response
                        log_msg = f"{datetime.utcnow()}: [{author_name}] {message_body}\n"
                        print(log_msg, end="")
                        with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                            chatlog_file.write(log_msg)
                        
                        # Print on the terminal the time when the bot's cooldown expires
                        print(f"Next response: {self.next_youtube_reply}", end="\r")
                
                # Retrieve the next batch of messages
                parsed_old_messages = True
                while True:

                    # Wait some time before retrieving the next messages
                    # Note: The API's response tells how long to wait in the field "pollingIntervalMillis".
                    # That time usually is around 3 seconds. But if we use that time, we are going to run
                    # out of quota in 1h 40min. So we are going to wait for 15 seconds instead, so we can
                    # last for a little over 6h.
                    sleep(15)
                    
                    retry_count = 0
                    messages_request = self.youtube_chat_get.liveChatMessages().list(
                        liveChatId=self.youtube_chat_id,
                        part="snippet,authorDetails",
                        pageToken=messages_results["nextPageToken"]
                    )
                    try:
                        with self.youtube_lock:
                            messages_results = messages_request.execute()
                        break
                    
                    except googleapiclient.errors.HttpError:
                        # The request raises an error if the stream has ended
                        self.youtube_error_log()
                        is_streaming = False
                        break
                    
                    except (socket.timeout, BrokenPipeError, ConnectionResetError):
                        # Wait then retry if there was a timeout
                        self.youtube_error_log()
                        retry_count += 1
                        if retry_count > 5: break
                        sleep(2 ** retry_count)
                    
                    except KeyError:
                        # Begin retrieving the chat again if there was no "nextPageToken"
                        self.youtube_error_log()
                        parsed_old_messages = False
                        messages_request = self.youtube_chat_get.liveChatMessages().list(
                            liveChatId=self.youtube_chat_id,
                            part="snippet,authorDetails",
                        )
                        try:
                            with self.youtube_lock:
                                messages_results = messages_request.execute()
                        except googleapiclient.errors.HttpError:
                            is_streaming = False
                            self.youtube_error_log()
                            break
                        except (socket.timeout, BrokenPipeError, ConnectionResetError):
                            self.youtube_error_log()
                            break
    
    def post_on_youtube_chat(self, message:str):
        """Post a message on the YouTube chat"""

        retry_count = 0
        
        while True:
            bot_response = self.youtube_chat_send.liveChatMessages().insert(
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
                with self.youtube_lock:
                    chat_post = bot_response.execute()
                self.raw_youtube_log(chat_post)
            
            except (googleapiclient.errors.HttpError, socket.timeout, BrokenPipeError, ConnectionResetError):
                self.youtube_error_log()
                retry_count += 1
                if retry_count > 5: return
                sleep(2 ** retry_count)
                continue
            
            self.cache_youtube_credentials()
            return
    
    def ai_response(self):
        """The AI responding the user's messages."""

        # Keep checking for new AI responses until the program is closed
        while self.running:
            response = self.output_queue.get()      # Wait for a new item at the output queue
            if response == STOP: break              # Exit if got the STOP signal
            platform, message_body, message_id, username = response   # Get the response's contents and the ID of the message being replied to

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
                self.twitch_command(f"@reply-parent-msg-id={message_id} PRIVMSG {self.channel} :{message_body}")
            elif platform == YOUTUBE:
                if message_id is not None:
                    self.post_on_youtube_chat(f"@{message_id} {message_body}")
                else:
                    self.post_on_youtube_chat(f"{message_body}")

            # Log the response
            log_msg = f"{datetime.utcnow()}: [{self.user}] {message_body}\n"
            print(log_msg, end="")
            with open(self.chatlog, "at", encoding="utf-8") as chatlog_file:
                chatlog_file.write(log_msg)
            
            # Print on the terminal the time when the bot's cooldown expires
            if platform == TWITCH:
                print(f"Next response: {self.last_reply_time + self.cooldown}", end="\r")
            elif platform == YOUTUBE:
                print(f"Next response: {self.next_youtube_reply}", end="\r")
    
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
            "!duel",
            "!dance",
            "!fart",
            "!attack",
            "!hug"
        )
        
        # Time to wait between commands (chosen randomly between the min and max wait times)
        sv_command_cooldown = timedelta(seconds=randint(sv_min_wait, sv_max_wait))
        sv_last_command = datetime.utcnow()

        # Safeguard for the bot to not spam "!accept" messages
        sv_last_duel_time = datetime.min
        sv_duel_cooldown = timedelta(seconds=5.0)

        # Send the commands after the cooldown time has elapsed
        while self.running:
            
            # Current time (UTC)
            now = datetime.utcnow()

            # Check which chats are active
            active_on_twitch = (now - self.last_reply_time) < self.cooldown
            active_on_youtube = now < self.next_youtube_reply
            
            # The bot only sends commands if the chat is active
            if (not active_on_twitch) and (not active_on_youtube):
                sleep(5.0)
                continue
            
            # Random commands (hug, attack, duel, dance, fart)
            sv_last_command_age = now - sv_last_command
            if (sv_last_command_age > sv_command_cooldown):
                sv_command = choice(sv_commands)
                if active_on_twitch:
                    self.twitch_command(f"PRIVMSG {self.channel} :{sv_command} {self._twitch_last_seen_user}")
                if active_on_youtube:
                    self.post_on_youtube_chat(f"{sv_command} {self._youtube_last_seen_user}")
                sv_command_cooldown = timedelta(seconds=randint(sv_min_wait, sv_max_wait))
                sv_last_command = datetime.utcnow()
            
            # Accept an incoming duel
            if self.duel:
                last_duel_age = now - sv_last_duel_time
                if (last_duel_age > sv_duel_cooldown):
                    sleep(1.5)  # Give the StreamAvatars some time to process the duel request on its side
                    if self.duel == TWITCH:
                        self.twitch_command(f"PRIVMSG {self.channel} :!accept")
                    elif self.duel == YOUTUBE:
                        self.post_on_youtube_chat("!accept")
                    sv_last_duel_time = now
                self.duel = False

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
        
        # Cache the login info so it does not need to be entered again on bot's restart
        try:
            self.cache_youtube_credentials()
        except AttributeError:
            pass
        
        # Close the connection
        self.input_queue.put_nowait(STOP)
        self.output_queue.put_nowait(STOP)
        self.running = False
        if not self.auth_failed: self.twitch_command("QUIT")
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
        youtube_channel_id = "UCMnULQ6F6kLDAHxofDWIbrw",
    )