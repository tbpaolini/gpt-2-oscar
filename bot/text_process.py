import re

RESPONSE_REGEX = re.compile(r"(?s)[A-Z].+[.!?\n]")
TAGS_REGEX = re.compile(r"<.+?>")
SPACES_REGEX = re.compile(r"\s{2,}")
NEWLINE_REGEX = re.compile(r"(\w)\n")
INNER_DOT_REGEX = re.compile(r"(?i)([A-Z])(\.)([A-Z])")
HTTP_REGEX = re.compile(r"(?i)https?://")
USERNAME_REGEX = re.compile(r"(?i)@{0,1}oscar(?:_{0,2}bot| \[bot\])")
LIST_REGEX = re.compile(r"([A-Z].*?:(?: [A-Z]{0,1})[^.\n]*?)(?:(?<!\.) )(?=[A-Z])")

def pre_process(text:str) -> str:
    """Filters the message before submitting it to the AI to respond."""
    
    # Replace by the word "OScar" the mentions to the bot
    text = USERNAME_REGEX.sub("OScar", text)

    return text

def post_process(text_input:str) -> str:
    """Filters the bot's response, so it begins and ends at a full sentence.
    Also it removes the tags that the bot sometimes outputs."""
    
    # Crop the output so it begins and end at a sentence
    # (we are considering that a sentence begins at a capital leter,
    #  and ends at a dot, exclamation, or question mark.)
    text_match = RESPONSE_REGEX.search(text_input)
    text_output = text_match[0] if text_match is not None else text_input

    # Add a space after a dot between letters
    # (prevents bot from posting links)
    text_output = INNER_DOT_REGEX.sub(r"\g<1>\g<2> \g<3>", text_output)

    # Insert periods between lists:
    # List A: item 1a, item 2a. List B: item 1b, item 2b
    #                         ^
    # (sometimes the bot makes constructs like that, and they look weird without the period)
    text_output = LIST_REGEX.sub(r"\g<1>. ", text_output)

    # Remove 'https://' and 'http://'
    text_output = HTTP_REGEX.sub("", text_output)

    # Remove line breaks and extraneous spaces
    text_output = TAGS_REGEX.sub("", text_output)           # Remove XML tags that somehow ended in the output
    text_output = NEWLINE_REGEX.sub("\g<1>. ", text_output) # Add a period to the lines ending without it
    text_output = text_output.replace("\n", " ")            # Replace line breaks by spaces
    text_output = SPACES_REGEX.sub(" ", text_output)        # Replace consecutive spaces by a single space

    # Remove the @ so the bot do not tag anyone
    text_output = text_output.replace("@", "(at)")

    return text_output