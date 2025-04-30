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
from tqdm import tqdm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = Flask(__name__, template_folder=".")

SERVER_URL = "http://10.20.36.113:5000"
CLIENT_PORT = 6001  # Change for each client (6002, 6003, etc.)
SETUP_FILE = "./client_setup_done"
SYNC_FOLDER = ""
CLIENT_ID = ""
METADATA_FILE = ""
SYNC_TIME = 30
SERVER_NAME = ""
currently_downloading = False

# ------------------ Setup and Configuration ------------------

@app.route("/")
def index():
    return render_template("setup.html")

@app.route("/submit", methods=["POST"])
def handle_form():
    global CLIENT_ID, SYNC_FOLDER, METADATA_FILE, SERVER_URL, SERVER_NAME, SYNC_TIME

    CLIENT_ID = request.form["client_id"]
    SYNC_FOLDER = request.form["folder_path"]
    SERVER_URL = f"http://{request.form['server_url']}:5000"
    METADATA_FILE = f"./{CLIENT_ID}_metadata.json"
    SYNC_TIME = int(request.form["sync_duration"])
    fetch_server_name()

    with open(SETUP_FILE, "w") as f:
        f.write(f"{CLIENT_ID}\n{SYNC_FOLDER}\n{SERVER_URL}\n{SERVER_NAME}\n{SYNC_TIME}")

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

    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()

    local_ip = get_local_ip()
    
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

def fetch_server_name():
    global SERVER_NAME
    try:
        response = requests.get(f"{SERVER_URL}/server_name", timeout=5)
        if response.status_code == 200:
            SERVER_NAME = response.json().get("name")
            print(f"Connected to server: {SERVER_NAME}")
        else:
            print("Failed to fetch server name.")
    except Exception as e:
        print(f"Error fetching server name: {e}")

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
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load metadata from {METADATA_FILE}: {e}")
            return {}  # Return empty metadata if file corrupt or unreadable
    else:
        return {}

def save_metadata(metadata):
    try:
        with open(METADATA_FILE, "w") as f:
            json.dump(metadata, f, indent=4)
    except IOError as e:
        print(f"Error: Failed to save metadata to {METADATA_FILE}: {e}")

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
            print("Server offline... Trying local rediscovery...")
            rediscover_server_locally()
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
    metadata = load_metadata()
    while True:
        try:
            requests.post(f"{SERVER_URL}/update_metadata", json={"client_id": CLIENT_ID, "metadata": metadata})
            print("Updated metadata with server.")
            break
        except requests.exceptions.RequestException:
            print("Failed to update server metadata. Retrying in 60 seconds...")
            rediscover_server_locally()
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
    global currently_downloading
    file_url = f"http://{peer_ip}:{peer_port}/download/{file_name}"
    try:
        currently_downloading = True
        start_time = time.time()
        response = requests.get(file_url, stream=True, timeout=10)
        if response.status_code == 200:
            total_size = int(response.headers.get('content-length', 0))
            file_path = os.path.join(SYNC_FOLDER, file_name)
            with open(file_path, "wb") as f:
                for chunk in tqdm(response.iter_content(chunk_size=4096), total=total_size//4096, unit='KB', unit_scale=True, desc=file_name):
                    if chunk:
                        f.write(chunk)

        end_time = time.time()
        duration = end_time - start_time
        print(f"Downloaded {file_name} from {peer_ip}:{peer_port} at {peer_folder} in {duration:.2f} seconds.")
        update_file_metadata(file_path)
        update_server_metadata()
    except requests.exceptions.RequestException:
        print(f"Failed to download {file_name} from {peer_ip}:{peer_port}")
    finally:
        currently_downloading = False

# ------------------ Watchdog Monitoring ------------------

class SyncFolderMonitor(FileSystemEventHandler):
    def on_created(self, event):
        global currently_downloading
        if currently_downloading:
            return
        if not event.is_directory:
            print(f"New file detected: {event.src_path}")
            update_file_metadata(event.src_path)

    def on_deleted(self, event):
        global currently_downloading
        if currently_downloading:
            return
        if not event.is_directory:
            file_name = os.path.basename(event.src_path)
            notify_server_file_deleted(file_name)

    def on_modified(self, event):
        global currently_downloading
        if currently_downloading:
            return
        if not event.is_directory:
            print(f"File modified: {event.src_path}")
            update_file_metadata(event.src_path)

def update_file_metadata(file_path):
    metadata = load_metadata()
    file_name = os.path.basename(file_path)

    # Retry logic for locked/incomplete files
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            if os.path.exists(file_path):
                metadata[file_name] = {
                    "size": os.path.getsize(file_path),
                    "hash": compute_file_hash(file_path),
                    "last_modified": os.path.getmtime(file_path)
                }
                save_metadata(metadata)
                return
        except (PermissionError, OSError) as e:
            print(f"Error reading file {file_path}: {e}. Retrying ({attempt+1}/{max_attempts})...")
            time.sleep(1)

    print(f"Skipping {file_path} after {max_attempts} failed attempts.")

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

# ------------------ Handling Dynamic IP change ------------------

def rediscover_server_locally():
    global SERVER_URL
    port = 5000
    target_name = SERVER_NAME

    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    ip_parts = local_ip.split('.')
    subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0/24"
    network = ipaddress.ip_network(subnet, strict=False)

    for ip in network.hosts():
        try:
            r = requests.get(f"http://{ip}:{port}/server_name", timeout=0.5)
            if r.status_code == 200 and r.json().get("name") == target_name:
                print(f"Rediscovered server at new IP: {ip}")
                SERVER_URL = f"http://{ip}:{port}"
                return True
        except:
            pass

    print("Could not rediscover server locally.")
    return False

# ------------------ Periodic Tasks ------------------

def server_check():
    while True:
        try:
            if currently_downloading:
                print("Currently downloading. Skipping sync check this time.")
            else:
                requests.get(f"{SERVER_URL}/get_clients", timeout=5)
                update_server_metadata()
                check_sync()
            time.sleep(SYNC_TIME)
        except requests.exceptions.RequestException:
            print("Server offline... Trying local rediscovery...")
            rediscover_server_locally()
            time.sleep(60)

def start_sync_process():
    initialize_client()
    register_with_server()
    scan_folder()
    update_server_metadata()

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
    #         SERVER_NAME = lines[3]
    #         SYNC_TIME = int(lines[4])
    #         METADATA_FILE = f"./{CLIENT_ID}_metadata.json"

    #     threading.Thread(target=start_sync_process, daemon=True).start()
    #     app.run(host="0.0.0.0", port=CLIENT_PORT)