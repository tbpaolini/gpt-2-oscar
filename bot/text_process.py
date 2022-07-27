import re

RESPONSE_REGEX = re.compile(r"(?s)[A-Z].+[.!?\n]")
TAGS_REGEX = re.compile(r"<.+?>")
SPACES_REGEX = re.compile(r"\s{2,}")
NEWLINE_REGEX = re.compile(r"(\w)\n")

def pre_process(text:str) -> str:
    """Filters the message before submitting it to the AI to respond."""
    return text.replace("@OScar__bot", " ", 1)   # Remove the bot's username

def post_process(text_input:str) -> str:
    """Filters the bot's response, so it begins and ends at a full sentence.
    Also it removes the tags that the bot sometimes outputs."""
    
    global RESPONSE_REGEX, TAGS_REGEX, SPACES_REGEX, NEWLINE_REGEX
    
    # Crop the output so it begins and end at a sentence
    # (we are considering that a sentence begins at a capital leter,
    #  and ends at a dot, exclamation, or question mark.)
    text_match = RESPONSE_REGEX.search(text_input)
    text_output = text_match[0] if text_match is not None else text_input

    # Remove line breaks and extraneous spaces
    text_output = NEWLINE_REGEX.sub("\g<1>. ", text_output) # Add a period to the lines ending without it

    # Remove the @ so the bot do not tag anyone
    text_output = text_output.replace("@", "(at)")

    return text_output