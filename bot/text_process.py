import re

RESPONSE_REGEX = re.compile(r"(?s)[A-Z].+[.!?\n]")
TAGS_REGEX = re.compile(r"<.+?>")
SPACES_REGEX = re.compile(r"\s{2,}")

def pre_process(text:str) -> str:
    """Filters the message before submitting it to the AI to respond."""
    return text.replace("@OScar__bot", "", 1)   # Remove the bot's username

def post_process(text_input:str) -> str:
    """Filters the bot's response, so it begins and ends at a full sentence.
    Also it removes the tags that the bot sometimes outputs."""
    
    global RESPONSE_REGEX, TAGS_REGEX, SPACES_REGEX
    
    # Crop the output so it begins and end at a sentence
    # (we are considering that a sentence begins at a capital leter,
    #  and ends at a dot, exclamation, or question mark.)
    text_match = RESPONSE_REGEX.search(text_input)
    text_output = text_match[0] if text_match is not None else text_input

    # Remove line breaks and extraneous spaces
    text_output = TAGS_REGEX.sub("", text_output)
    text_output = text_output.replace("\n", " ")
    text_output = SPACES_REGEX.sub(" ", text_output)

    # Remove the @ so the bot do not tag anyone
    text_out_put = text_output.replace("@", "")

    return text_output