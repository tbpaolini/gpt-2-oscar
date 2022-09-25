"""
    This script is for making the login on the bot's YouTube account, and then
    saving the credentials to file, so it is not necessary to make a login each
    time the bot is restarted.

    The reasons this script exists is because the run_console() authentication
    flow does not work for applications flagged as being "in production", and
    I do not want to open a port on my server just to use the run_local_server()
    flow.

    The idea of this script is to be run on my local computer, then I upload to
    the server the file with the credentials. Apps that flagged as "in testing"
    have their credentials to expire after 7 days, while the ones flagged as
    "in production" do not have an expiration time.
"""

import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
import pickle

def test():
    """Test if an existing credentials file is working."""
    with open("auth.bin", "rb") as file:
        youtube_client = pickle.load(file)
    request = youtube_client.channels().list(
        part="id",
        mine=True
    )
    print(request.execute())

if __name__ == "__main__":
    scopes = ["https://www.googleapis.com/auth/youtube.force-ssl"]
    api_service_name = "youtube"
    api_version = "v3"
    client_secrets_file = "google_client_secrets.json"

    # Get credentials and create an API client
    # (this is going to open a browser window for manually logging in the bot's YouTube account)
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        client_secrets_file,
        scopes
    )
    credentials = flow.run_local_server()

    # YouTube API client for the bot
    youtube_client = googleapiclient.discovery.build(
        api_service_name,
        api_version,
        credentials=credentials
    )

    # Cache the credentials to file
    with open("auth.bin", "wb") as file:
        pickle.dump(youtube_client, file)