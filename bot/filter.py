import bz2, base64, pickle
from pathlib import Path

# Note: I have obfuscated the word filter so the words do not appear in plaintext
with open(Path(__file__).parent / "filter.bin", "rb") as file:
    filter_bz2 = file.read()
filter_b64 = bz2.decompress(filter_bz2)
filter_str = base64.b64decode(filter_b64)
filter_set = pickle.loads(filter_str)

# Returns true if the text has not any word from the filter
def is_okay(text:str):
    words_list = []
    for word in text.lower().split():
        token = "".join(letter for letter in word if letter.isalnum())
        if token in filter_set:
            return False
    
    return True