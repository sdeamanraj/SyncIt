import hashlib
import ipaddress
import json
import os
import requests
import socket
import time
import threading
import webbrowser
from flask import Flask, jsonify, send_file, request, render_template
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = Flask(__name__, template_folder=".")

SERVER_URL = "http://192.168.1.7:5000"
CLIENT_PORT = 6001  # Change for each client (if running in same system)
SETUP_FILE = "./client_setup_done"
SYNC_FOLDER = ""
CLIENT_ID = ""
METADATA_FILE = ""
SYNC_TIME = 30

# ------------------ Setup and Configuration ------------------

@app.route("/")
def index():
    return render_template("setup.html")

@app.route("/submit", methods=["POST"])
def handle_form():
    global CLIENT_ID, SYNC_FOLDER, METADATA_FILE, SERVER_URL, SYNC_TIME

    CLIENT_ID = request.form["client_id"]
    SYNC_FOLDER = request.form["folder_path"]
    SERVER_URL = f"http://{request.form['server_url']}:5000"
    METADATA_FILE = f"./{CLIENT_ID}_metadata.json"
    SYNC_TIME = int(request.form["sync_duration"])

    with open(SETUP_FILE, "w") as f:
        f.write(f"{CLIENT_ID}\n{SYNC_FOLDER}\n{SERVER_URL}\n{SYNC_TIME}")

    threading.Thread(target=start_sync_process, daemon=True).start()
    return '''
    <script>
        alert("✅ Client registered successfully");
        if (window.close) {
            window.close();
        } else {
            document.body.innerHTML = "<h3 style='text-align:center; margin-top:20%; font-family:sans-serif;'>Setup completed. You can now close this tab.</h3>";
        }
    </script>
    '''

@app.route("/discover_servers")
def discover_servers():
    port = 5000
    discovered = []

    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    ip_parts = local_ip.split('.')
    subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0/24"
    network = ipaddress.ip_network(subnet, strict=False)

    def check_server(ip):
        try:
            r = requests.get(f"http://{ip}:{port}/server_name", timeout=0.4)
            if r.status_code == 200:
                name = r.json().get("name", f"Server @ {ip}")
                discovered.append({"ip": ip, "name": name})
        except:
            pass

    threads = []
    for ip in network.hosts():  
        # if str(ip) == local_ip:
        #     continue  # Skip own IP
        t = threading.Thread(target=check_server, args=(str(ip),))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return jsonify(discovered)

# ------------------ File Sync and Metadata ------------------

def compute_file_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def initialize_client():
    if not os.path.exists(SYNC_FOLDER):
        os.makedirs(SYNC_FOLDER)
    if not os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "w") as f:
            json.dump({}, f)

def load_metadata():
    with open(METADATA_FILE, "r") as f:
        return json.load(f)

def save_metadata(metadata):
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=4)

def register_with_server():
    while True:
        try:
            response = requests.post(f"{SERVER_URL}/register", json={
                "client_id": CLIENT_ID,
                "port": CLIENT_PORT,
                "sync_folder": SYNC_FOLDER
            })
            print(response.json())
            return True
        except requests.exceptions.RequestException:
            print("Server unreachable. Retrying in 60 seconds...")
            time.sleep(60)

def scan_folder():
    metadata = {}
    for file_name in os.listdir(SYNC_FOLDER):
        file_path = os.path.join(SYNC_FOLDER, file_name)
        if os.path.isfile(file_path):
            metadata[file_name] = {
                "size": os.path.getsize(file_path),
                "hash": compute_file_hash(file_path),
                "last_modified": os.path.getmtime(file_path)
            }
    save_metadata(metadata)
    return metadata

def update_server_metadata():
    metadata = scan_folder()
    while True:
        try:
            requests.post(f"{SERVER_URL}/update_metadata", json={"client_id": CLIENT_ID, "metadata": metadata})
            print("Updated metadata with server.")
            break
        except requests.exceptions.RequestException:
            print("Failed to update server metadata. Retrying in 60 seconds...")
            time.sleep(60)

def notify_server_file_deleted(file_name):
    while True:
        try:
            response = requests.post(f"{SERVER_URL}/delete_file", json={
                "client_id": CLIENT_ID,
                "file_name": file_name
            })
            print(response.json()["message"])
            break
        except requests.exceptions.RequestException:
            print("Failed to notify server about deletion. Retrying in 60 seconds...")
            time.sleep(60)

def check_sync():
    while True:
        try:
            response = requests.get(f"{SERVER_URL}/sync")
            sync_instructions = response.json()

            if CLIENT_ID in sync_instructions:
                instructions = sync_instructions[CLIENT_ID]

                for file_name in instructions.get("delete_files", []):
                    delete_local_file(file_name)

                for missing_file, peer_info in instructions.items():
                    if missing_file == "delete_files":
                        continue
                    peer_ip, peer_port, peer_folder = peer_info["ip"], peer_info["port"], peer_info["sync_folder"]
                    if peer_ip and peer_port:
                        download_file_from_peer(missing_file, peer_ip, peer_port, peer_folder)
            break
        except requests.exceptions.RequestException:
            print("Sync check failed. Retrying in 60 seconds...")
            time.sleep(60)

def delete_local_file(file_name):
    file_path = os.path.join(SYNC_FOLDER, file_name)
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Deleted {file_name} from {SYNC_FOLDER}.")

def download_file_from_peer(file_name, peer_ip, peer_port, peer_folder):
    file_url = f"http://{peer_ip}:{peer_port}/download/{file_name}"
    try:
        response = requests.get(file_url, stream=True, timeout=10)
        if response.status_code == 200:
            file_path = os.path.join(SYNC_FOLDER, file_name)
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
            print(f"Downloaded {file_name} from {peer_ip}:{peer_port} at {peer_folder}")
    except requests.exceptions.RequestException:
        print(f"Failed to download {file_name} from {peer_ip}:{peer_port}")

# ------------------ Watchdog Monitoring ------------------

class SyncFolderMonitor(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            print(f"New file detected: {event.src_path}")
            scan_folder()
            update_server_metadata()

    def on_deleted(self, event):
        if not event.is_directory:
            file_name = os.path.basename(event.src_path)
            notify_server_file_deleted(file_name)
            scan_folder()
            update_server_metadata()

    def on_modified(self, event):
        if not event.is_directory:
            print(f"File modified: {event.src_path}")
            scan_folder()
            update_server_metadata()

def start_monitoring():
    observer = Observer()
    observer.schedule(SyncFolderMonitor(), SYNC_FOLDER, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

# ------------------ Periodic Tasks ------------------

def server_check():
    while True:
        time.sleep(SYNC_TIME)
        try:
            requests.get(f"{SERVER_URL}/get_clients", timeout=5)
            print("Server is back online!")
            check_sync()
        except requests.exceptions.RequestException:
            print("Server still offline...")
            time.sleep(60)

def periodic_metadata_updater():
    while True:
        scan_folder()
        update_server_metadata()
        time.sleep(SYNC_TIME)

def start_sync_process():
    initialize_client()
    register_with_server()
    update_server_metadata()

    threading.Thread(target=periodic_metadata_updater, daemon=True).start()
    threading.Thread(target=server_check, daemon=True).start()
    start_monitoring()

# ------------------ File Server Endpoints ------------------

@app.route("/download/<filename>", methods=["GET"])
def serve_file(filename):
    file_path = os.path.join(SYNC_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return {"message": "File not found"}, 404

@app.route("/delete_file", methods=["POST"])
def delete_requested_file():
    data = request.json
    file_name = data.get("file_name")

    delete_local_file(file_name)
    metadata = load_metadata()
    if file_name in metadata:
        del metadata[file_name]
        save_metadata(metadata)

    return jsonify({"message": f"File {file_name} deleted from {SYNC_FOLDER}."})

# ------------------ Main Entry ------------------

if __name__ == "__main__":
    # if not os.path.exists(SETUP_FILE):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{CLIENT_PORT}")).start()
        app.run(host="0.0.0.0", port=CLIENT_PORT)
    # else:
    #     with open(SETUP_FILE, "r") as f:
    #         lines = f.read().splitlines()
    #         CLIENT_ID = lines[0]
    #         SYNC_FOLDER = lines[1]
    #         SERVER_URL = lines[2]
    #         SYNC_TIME = int(lines[3])
    #         METADATA_FILE = f"./{CLIENT_ID}_metadata.json"

    #     threading.Thread(target=start_sync_process, daemon=True).start()
    #     app.run(host="0.0.0.0", port=CLIENT_PORT)