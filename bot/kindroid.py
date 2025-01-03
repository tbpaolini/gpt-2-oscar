from __future__ import annotations

import http.client
import ssl
import json
import multiprocessing as mp
from time import sleep
from os import getenv

# "Macros" for which platforms the bot is interacting with
TWITCH  = "twitch"
YOUTUBE = "youtube"

# Sensor objects
STOP = "STOP"
SUCCESS = "SUCCESS"

# # Type of the input and output queues
# type UserText = mp.Queue[tuple[str,str,str,str]]

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

    # HTTP client for the Kindroid API
    kin = __kindroid_connect()

    # Endpoints of the Kindroid API
    kin_send = "/v1/send-message"
    kin_restart = "/v1/chat-break"

    # Max amount of characters in a response
    CHAR_LIMIT = 200
    
    # Listen and respond to messages
    while True:
        
        # Get the next user message from the input queue
        next_item = input_queue.get()
        if next_item == STOP:
            output_queue.put(STOP, block=False)
            break
        platform, raw_text, response_id, username = next_item

        # Change the character limit based on the platform
        if platform == TWITCH:
            CHAR_LIMIT = 500
        elif platform == YOUTUBE:
            CHAR_LIMIT = 200

        # HTTP headers
        headers = {
            "User-Agent" : AGENT,
            "Content-Type" : "application/json",
            "Authorization" : "Bearer " + KIN_KEY,
        }

        # Instruct the AI to respond within the character limit
        OOC = f"(OOC: respond using up to {CHAR_LIMIT} characters.)"

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
                    temp_message = f"(OOC: Rewrite the text below so it's under the {CHAR_LIMIT}-characters limit. "
                    "Trim less important information as needed. "
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
                kin_message = "*OScar showed a sad face then crashed. "
                f"The blue screen says: {resp.status} {resp.reason}, I'LL BE BACK!*"

        else: # status_msg != SUCCESS
            # When the message failed because of a local error (not Kindroid's servers)
            kin_message = f"*OScar just exploded. A ghostly voice whispered these words: {status_msg}.*"
        
        # Sumbit the response to the output queue
        output_queue.put((platform, kin_message[:CHAR_LIMIT], response_id, username), block=False)
