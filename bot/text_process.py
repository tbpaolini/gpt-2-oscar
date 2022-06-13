import re

RESPONSE_REGEX = re.compile(r"(?s)[A-Z].+[.!?\n]")
TAGS_REGEX = re.compile(r"<.+?>")
SPACES_REGEX = re.compile(r"\s{2,}")

def pre_process(text:str) -> str:
    pass

def post_process(text_input:str) -> str:
    """Filters the bot's response, so it begins and ends at a full sentence.
    Also it removes the tags that the bot sometimes outputs."""
    
    global RESPONSE_REGEX, TAGS_REGEX, SPACES_REGEX
    text_match = RESPONSE_REGEX.search(text_input)
    text_output = text_match[0] if text_match is not None else text_input

    text_output = TAGS_REGEX.sub("", text_output)
    text_output = text_output.replace("\n", " ")
    text_output = SPACES_REGEX.sub(" ", text_output)

    return text_output