import os
import dropbox
import time
import json
import threading
import screeninfo
import requests
import numpy as np
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime, timezone
from flask_cors import CORS
import re

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------
#                          Dropbox Setup
# ---------------------------------------------------------------------

DROPBOX_REFRESH_TOKEN = "nhQ3OlKks44AAAAAAAAAATiGAqgUcXK3J7ulT1-pLFHv_gI3eyoIl7JGq6mSdkDj"
DROPBOX_APP_KEY = "cb1znnqy6mc1kni"
DROPBOX_APP_SECRET = "erbwa2bmaf12m5b"
DROPBOX_ACCESS_TOKEN = None  # Store the refreshed token


def refresh_dropbox_token():
    """Refreshes Dropbox access token and updates the global variable."""
    global DROPBOX_ACCESS_TOKEN
    url = "https://api.dropbox.com/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": DROPBOX_REFRESH_TOKEN,
        "client_id": DROPBOX_APP_KEY,
        "client_secret": DROPBOX_APP_SECRET,
    }
    response = requests.post(url, data=data)
    token_data = response.json()
    if "access_token" in token_data:
        DROPBOX_ACCESS_TOKEN = token_data["access_token"]
        print("✅ Dropbox token refreshed successfully.")
    else:
        print(f"❌ Error refreshing token: {token_data}")
        DROPBOX_ACCESS_TOKEN = None
        raise Exception("Failed to refresh Dropbox token!")

def get_dropbox_client():
    """Returns Dropbox client with an auto-refreshed token."""
    refresh_dropbox_token()
    return dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

def upload_to_dropbox(file_path, dropbox_path):
    """Uploads a file to Dropbox and removes the local copy."""
    try:
        dbx = get_dropbox_client()
        with open(file_path, "rb") as f:
            dbx.files_upload(
                f.read(),
                dropbox_path,
                mode=dropbox.files.WriteMode("overwrite")
            )
        os.remove(file_path)
        print(f"✅ Uploaded {file_path} to Dropbox.")
    except Exception as e:
        print(f"❌ Dropbox upload failed: {str(e)}")

# ---------------------------------------------------------------------
#                   Global Test/Recording State
# ---------------------------------------------------------------------
tests = {}
recording_flags = {}

def extract_drive_id(drive_url):
    """Extracts a Google Drive file ID from multiple link formats."""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)/", drive_url)
    return match.group(1) if match else drive_url

@app.route("/")
def dashboard():
    """Displays a dashboard with active tests."""
    return render_template("dashboard.html", tests=tests, datetime=datetime)

# ---------------------------------------------------------------------
#                            Create Test
# ---------------------------------------------------------------------
@app.route("/create_test", methods=["POST"])
def create_test():
    test_id = str(int(time.time()))
    start_time = request.form.get("start_time")
    end_time = request.form.get("end_time")
    duration = request.form.get("duration")
    question_link = request.form.get("question_link")
    answer_link = request.form.get("answer_link")

    if not all([start_time, end_time, duration, question_link, answer_link]):
        return jsonify({"error": "All fields are required!"}), 400

    try:
        start_timestamp = datetime.strptime(start_time, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()
        end_timestamp = datetime.strptime(end_time, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()
        duration = int(duration)
    except ValueError:
        return jsonify({"error": "Invalid date format or duration!"}), 400

    max_duration = end_timestamp - start_timestamp
    if duration * 60 > max_duration:
        return jsonify({"error": "Duration exceeds allowed time range!"}), 400

    # Convert to Drive preview links
    q_id = extract_drive_id(question_link)
    a_id = extract_drive_id(answer_link)
    question_embed = f"https://drive.google.com/file/d/{q_id}/preview"
    answer_embed = f"https://drive.google.com/file/d/{a_id}/preview"

    tests[test_id] = {
        "start_time": start_timestamp,
        "end_time": end_timestamp,
        "duration": duration * 60,
        "question_link": question_embed,
        "answer_link": answer_embed,
        "submitted": False
    }

    link = url_for("waiting_page", test_id=test_id, _external=True)
    return jsonify({"message": "Test created!", "test_id": test_id, "test_link": link})

# ---------------------------------------------------------------------
#                           Waiting Page
# ---------------------------------------------------------------------
@app.route("/waiting/<test_id>")
def waiting_page(test_id):
    test = tests.get(test_id)
    if not test:
        return "Invalid test ID!", 404
    return render_template("waiting.html", test_id=test_id, test=test)

# ---------------------------------------------------------------------
#                            Test Page
# ---------------------------------------------------------------------
@app.route("/test/<test_id>")
def test_page(test_id):
    """Renders the test page with the embedded question PDF."""
    test = tests.get(test_id)
    if not test:
        return "Invalid test ID!", 404

    # Pass test["duration"] (in seconds) to the template
    return render_template(
        "test.html",
        test_id=test_id,
        pdf_link=test["question_link"],
        duration=test["duration"]   # in seconds
    )

# ---------------------------------------------------------------------
#                          Answers Page
# ---------------------------------------------------------------------
@app.route("/answers/<test_id>")
def answers_page(test_id):
    """Displays answer PDF only if the test was submitted."""
    test = tests.get(test_id)
    if not test:
        return "Invalid test ID!", 404
    if not test["submitted"]:
        return "You have not submitted the test yet!", 403
    return render_template("answers.html", test_id=test_id, pdf_link=test["answer_link"])

# ---------------------------------------------------------------------
#                         Capture Snapshot
# ---------------------------------------------------------------------
@app.route("/capture_snapshot/<test_id>", methods=["POST"])
def capture_snapshot(test_id):
    """
    Accepts a snapshot blob from the client's canvas, saves it locally,
    and uploads to Dropbox.
    """
    snapshot_file = request.files.get("snapshot")
    if not snapshot_file:
        return jsonify({"error": "No snapshot file received!"}), 400

    ss_dir = f"{test_id}_snapshots"
    os.makedirs(ss_dir, exist_ok=True)

    file_name = snapshot_file.filename
    local_path = os.path.join(ss_dir, file_name)
    snapshot_file.save(local_path)

    if os.path.getsize(local_path) < 100:  # basic sanity check
        os.remove(local_path)
        return jsonify({"error": "Snapshot was empty."}), 400

    # Upload to Dropbox, remove local
    upload_to_dropbox(local_path, f"/Test_Recordings/{test_id}/Snapshots/{file_name}")
    return jsonify({"message": "Snapshot received and uploaded."}), 200

# ---------------------------------------------------------------------
#                         Tab Change Route
# ---------------------------------------------------------------------
@app.route("/tab_change/<test_id>", methods=["POST"])
def tab_change(test_id):
    """Logs tab switches to a local file and uploads to Dropbox."""
    log_file = f"{test_id}_activity.log"
    if not os.path.exists(log_file):
        with open(log_file, "w") as f:
            f.write("Test activity log started.\n")

    try:
        with open(log_file, "a") as f:
            f.write(f"Tab switched at {datetime.now(timezone.utc)}\n")
        upload_to_dropbox(log_file, f"/Test_Logs/{log_file}")
        return jsonify({"message": "Tab switch logged."}), 200
    except Exception as e:
        print(f"❌ Error logging tab change: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------
#                        Submit Test Route
# ---------------------------------------------------------------------
@app.route("/submit_test/<test_id>", methods=["POST"])
def submit_test(test_id):
    """
    Receives final client recordings of screen/webcam,
    plus any other files, then marks the test as submitted.
    """
    test = tests.get(test_id)
    if not test:
        return jsonify({"error": "Invalid test ID"}), 404
    if test["submitted"]:
        return jsonify({"error": "Test already submitted"}), 400

    # Grab screen + camera recordings from the form
    screen_file = request.files.get("screen_recording")
    camera_file = request.files.get("camera_recording")

    media_dir = f"{test_id}_final_media"
    os.makedirs(media_dir, exist_ok=True)
    uploaded_files = []

    def safe_save_upload(f, filename):
        """Helper to save each file and upload to Dropbox."""
        if f:
            local_path = os.path.join(media_dir, filename)
            f.save(local_path)
            upload_to_dropbox(local_path, f"/Final_Submissions/{test_id}/{filename}")
            uploaded_files.append(filename)

    # Save and upload the screen + camera recordings
    safe_save_upload(screen_file, "screen_recording.webm")
    safe_save_upload(camera_file, "camera_recording.webm")

    # Mark test as submitted
    test["submitted"] = True
    print(f"✅ Test {test_id} submitted with {uploaded_files}")
    return jsonify({"message": "Test submitted successfully!", "uploaded_files": uploaded_files}), 200

# ---------------------------------------------------------------------
#                           Run App
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))