import http.client
import ssl
import json
from os import getenv

# http.client.HTTPConnection.debuglevel = 1

message = input("Message: ")
# message = "Good day!"

KIN_ID = getenv("KINDROID_ID")
KIN_KEY = getenv("KINDROID_KEY")
AGENT = "Personal project (testing)"

host = "api.kindroid.ai"
method = "POST"
kin_send = "/v1/send-message"
kin_restart = "/v1/chat-break"

headers = {
    "User-Agent" : AGENT,
    "Content-Type" : "application/json",
    "Authorization" : "Bearer " + KIN_KEY,
}

body = json.dumps({
    "ai_id" : KIN_ID,
    "message" : message,
})

# print(body)
# exit()

context = ssl.create_default_context()
conn = http.client.HTTPSConnection(host=host, port=443, context=context)
conn.request(method="POST", url=kin_send, body=body, headers=headers)
resp = conn.getresponse()

print(resp.status, resp.reason)
print(resp.read().decode())

