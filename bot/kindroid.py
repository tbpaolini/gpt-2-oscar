from __future__ import annotations

import http.client
import ssl
import json
import multiprocessing as mp
import pickle
import re, bot.text_process
from pathlib import Path
from datetime import datetime, timedelta
from time import sleep
from os import getenv

# "Macros" for which platforms the bot is interacting with
TWITCH  = "twitch"
YOUTUBE = "youtube"

# Sensor objects
STOP = "STOP"
SUCCESS = "SUCCESS"

# For performing a chat break (it is like "rebooting" the AI)
PATH_LAST_RESET_TIME  = Path("last_reset.bin").absolute()
LAST_RESET_MAX_AGE = timedelta(hours=6.0)
DEFAULT_GREETING = "Welcome to The Tower Corp, newhire! Remember, you are "\
                   "here forever. I hope you are as excited as we are!"

# # Type of the input and output queues
# type UserText = mp.Queue[tuple[str,str,str,str]]

# We are overrinding the default response cropping regular expression,
# because Kindroid already does a good job at giving a well-formated response.
bot.text_process.RESPONSE_REGEX = re.compile(r"(?s).+")

def __kindroid_connect() -> http.client.HTTPSConnection:
    """(Re)create the secure HTTP client."""
    host = "api.kindroid.ai"
    ssl_context = ssl.create_default_context()
    return http.client.HTTPSConnection(host=host, port=443, context=ssl_context)

def __kindroid_request(client:http.client.HTTPSConnection, method, url, body, headers) -> str:
    """Attempt to send a message a few times, with increasing delay between requests.
    Returns a status message (string)."""
    
    status = ""
    count = 0
    max_tries = 6
    
    while count <= max_tries:
        try:
            client.request(method, url, body, headers)
            return SUCCESS
        except (http.client.HTTPException, OSError) as err:
            count += 1
            status = str(err)
            sleep(2**count)
    
    return status

def interact_model(
        input_queue:mp.Queue[tuple[str,str,str,str]],
        output_queue:mp.Queue[tuple[str,str,str,str]]
):
    """Main interface with the Kindroid AI."""

    # Credentials and user agent
    KIN_ID = getenv("KINDROID_ID")
    KIN_KEY = getenv("KINDROID_KEY")
    AGENT = "OScar Bot (personal project) - https://www.github.com/tbpaolini/gpt-2-oscar"

    # HTTP headers
    headers = {
        "User-Agent" : AGENT,
        "Content-Type" : "application/json",
        "Authorization" : "Bearer " + KIN_KEY,
    }

    # HTTP client for the Kindroid API
    kin = None

    print("-" * 40 + "\nBot is ready! Listening for messages.\n" + "-" * 40)

    # Endpoints of the Kindroid API
    kin_send = "/v1/send-message"
    kin_restart = "/v1/chat-break"

    # Max amount of characters in a response
    CHAR_LIMIT = 200

    # Set the last time of a chat break
    if not PATH_LAST_RESET_TIME.exists():
        # Forces a chat break in case no last reset time was found cached
        last_reset_time = datetime.utcnow() - LAST_RESET_MAX_AGE
        with open(PATH_LAST_RESET_TIME, "wb") as last_reset_file:
            pickle.dump(last_reset_time, last_reset_file)
    else:
        # Load the cached last reset time
        with open(PATH_LAST_RESET_TIME, "rb") as last_reset_file:
            last_reset_time = pickle.load(last_reset_file)
    
    # Listen and respond to messages
    while True:
        
        # Get the next user message from the input queue
        next_item = input_queue.get()
        if next_item == STOP:
            output_queue.put(STOP, block=False)
            if kin is not None: kin.close()
            break
        platform, raw_text, response_id, username = next_item

        # HTTP client for the Kindroid API
        kin = __kindroid_connect()

        # Perform a chat break if one was made over 6 hours ago
        # (the bot tends to get repetitive after a while, this mitigates the issue)
        current_time = datetime.utcnow()
        last_reset_age = current_time - last_reset_time
        if last_reset_age >= LAST_RESET_MAX_AGE:
            body = json.dumps({
                "ai_id" : KIN_ID,
                "greeting" : DEFAULT_GREETING,
            })
            status_msg = __kindroid_request(kin, method="POST", url=kin_restart, body=body, headers=headers)
            if status_msg == SUCCESS:
                resp = kin.getresponse()
                resp.read()
                if resp.status == 200:
                    last_reset_time = current_time
                    with open(PATH_LAST_RESET_TIME, "wb") as last_reset_file:
                        pickle.dump(last_reset_time, last_reset_file)

        # Change the character limit based on the platform
        if platform == TWITCH:
            CHAR_LIMIT = 500
        elif platform == YOUTUBE:
            CHAR_LIMIT = 200

        # Give enough room for the username
        CHAR_LIMIT -= (len(username) + 2)

        # Instruct the AI to respond within the character limit
        OOC = f"(OOC: respond under {CHAR_LIMIT} characters.)"

        # Request body in JSON format
        body = json.dumps({
            "ai_id" : KIN_ID,
            "message" : f"{username}: {raw_text}\n{OOC}"[:4000], # Absolute max of 4000 chars
        })

        # Submit the message to the AI
        status_msg = __kindroid_request(kin, method="POST", url=kin_send, body=body, headers=headers)

        # Decode the AI's response
        if status_msg == SUCCESS:
            resp = kin.getresponse()
            kin_message = resp.read().decode("utf-8", "replace")

            # If the HTTP request was successfull
            if resp.status == 200:
                if len(kin_message) > CHAR_LIMIT:
                    # Instruct the AI to trim down the message
                    temp_message = f"(OOC: Rewrite the text below so it's under the {CHAR_LIMIT}-characters limit. "\
                    "Trim less important information as needed. "\
                    f"Respond with nothing except the rewritten text)\n{kin_message}"
                    body = json.dumps({
                        "ai_id" : KIN_ID,
                        "message" : temp_message[:4000], # Absolute max of 4000 chars
                    })

                    # Request the AI for the trimmed down message
                    status_msg = __kindroid_request(kin, method="POST", url=kin_send, body=body, headers=headers)
                    if status_msg == SUCCESS:
                        resp = kin.getresponse()
                        if resp.status == 200:
                            kin_message = resp.read().decode("utf-8", "replace")
            
            # If the HTTP request failed
            else: # resp.status != 200
                kin_message = "*OScar showed a sad face then crashed. "\
                f"The blue screen says: {resp.status} {resp.reason}, I'LL BE BACK!*"

        else: # status_msg != SUCCESS
            # When the message failed because of a local error (not Kindroid's servers)
            kin_message = f"*OScar just exploded. A ghostly voice whispered these words: {status_msg}.*"
        
        # Sumbit the response to the output queue
        output_queue.put((platform, kin_message[:CHAR_LIMIT], response_id, username), block=False)
        kin.close()
