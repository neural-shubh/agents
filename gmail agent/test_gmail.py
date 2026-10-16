from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file("token.json")
service = build("gmail", "v1", credentials=creds)

profile = service.users().getProfile(userId="me").execute()
print("Connected as:", profile["emailAddress"])

res = service.users().messages().list(userId="me", maxResults=5).execute()
for m in res.get("messages", []):
    msg = service.users().messages().get(
        userId="me", id=m["id"], format="metadata",
        metadataHeaders=["From", "Subject"]).execute()
    h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
    print("-", h.get("From"), "|", h.get("Subject"))