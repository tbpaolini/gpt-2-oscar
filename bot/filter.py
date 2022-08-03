import bz2, base64, pickle
from pathlib import Path

# Note: I have obfuscated the word filter so the words do not appear in plaintext
filter_path = Path(__file__).parent / "filter.bin"
with open(filter_path, "rb") as file:
    filter_bz2 = file.read()
filter_b64 = bz2.decompress(filter_bz2)
filter_str = base64.b64decode(filter_b64)
filter_set = pickle.loads(filter_str)

# Returns true if the text has not any word from the filter
def is_okay(text:str):
    for word in text.lower().split():
        token = "".join(letter for letter in word if letter.isalnum())
        if token in filter_set:
            return False
    
    return True

# Remove from the word filter the words that appear in-game
def filter_trim():
    global filter_set
    paths = (Path(__file__).parents[1] / "dataset/cleaned").glob("*.txt")
    game_words = set()
    
    for path in paths:
        with open(path, "rt", encoding="utf-8") as file:
            for line in file:
                for word in line.lower().split():
                    game_words.add("".join(letter for letter in word if letter.isalnum()))
    
    filter_set -= game_words

# Save the word filter back to file
def save_filter():
    global filter_set, filter_path
    filter_str = pickle.dumps(filter_set)
    filter_b64 = base64.b64encode(filter_str)
    filter_bz2 = bz2.compress(filter_b64)
    with open(filter_path, "wb") as file:
        file.write(filter_bz2)