from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Must match the scopes you added under Data Access
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BASE = Path(__file__).parent
CREDS_FILE = BASE / "credentials.json"
TOKEN_FILE = BASE / "token.json"


def get_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    service = get_service()
    profile = service.users().getProfile(userId="me").execute()
    print("Connected as:", profile["emailAddress"])

    msgs = service.users().messages().list(userId="me", maxResults=5).execute()
    for m in msgs.get("messages", []):
        full = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject"],
        ).execute()
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        print("-", headers.get("From"), "|", headers.get("Subject"))
