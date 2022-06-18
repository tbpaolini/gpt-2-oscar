import re
from pathlib import Path

# Folders of the raw dataset and its cleaned output
dataset_path = Path("raws")
cleaned_path = Path("cleaned")

# Regular expressions for cleaning the dataset
string_regex = re.compile(r'"(.+)"')        # Get the strings
tokens_regex = re.compile(r'([%~$^\*]\d+_)')  # Get the text's markup
spaces_regex = re.compile(r'(\s{2,})')   # Get sequences of 2 or more blank spaces

# Get the two variations of the rotating texts
rotating_text_left  = re.compile(r'(?:@(.+?)@.+?@)')
rotating_text_right = re.compile(r'(?:@.+?@(.+?)@)')

# Loop through the files on the dataset folders
for input_path in dataset_path.glob("*_ENUS.gml"):
    
    # Read the entire text content of the file
    with open(input_path, "rt") as input_file:
        raw_contents = input_file.read()
    
    # Get the strings
    raw_lines = string_regex.findall(raw_contents)

    # Process each line of text
    output_path = Path(cleaned_path, input_path.stem + ".txt")
    with open(output_path, "wt") as output_file:
        for line in raw_lines:
            clean_line = tokens_regex.sub("", line)     # Remove markup tokens from the text
            clean_line = clean_line.replace("#", " ")   # Replace the game's newline character (#) by a space
            clean_line = spaces_regex.sub(" ", clean_line).strip()  # Remove extraneous blank spaces
            
            # Write the line to the output
            if "@" in clean_line:
                # Create two versions of the lines with rotating text
                clean_line_left  = rotating_text_left.sub(r"\1", clean_line)
                clean_line_right = rotating_text_right.sub(r"\1", clean_line)
                output_file.write(f"{clean_line_left}\n{clean_line_right}\n")
            else:
                output_file.write(clean_line + "\n")
