from flask import Flask, request, jsonify
import os
import json
import requests
import socket

app = Flask(__name__)

METADATA_FILE = "./server_metadata.json"
CLIENTS_FILE = "./clients.json"

# ------------------ File Management ------------------

def initialize_files():
    if not os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "w") as f:
            json.dump({}, f)
    if not os.path.exists(CLIENTS_FILE):
        with open(CLIENTS_FILE, "w") as f:
            json.dump({}, f)

def load_metadata():
    if not os.path.exists(METADATA_FILE):
        return {}
    with open(METADATA_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_metadata(metadata):
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=4)

def load_clients():
    if not os.path.exists(CLIENTS_FILE):
        return {}
    with open(CLIENTS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_clients(new_clients):
    try:
        existing_clients = load_clients()
        existing_clients.update(new_clients)
    except json.JSONDecodeError:
        existing_clients = new_clients
    with open(CLIENTS_FILE, "w") as f:
        json.dump(existing_clients, f, indent=4)

# ------------------ Network Utility ------------------

def is_client_alive(ip, port, timeout=2):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except:
        return False

# ------------------ Client Endpoints ------------------

@app.route("/register", methods=["POST"])
def register_client():
    data = request.json
    client_id = data.get("client_id")
    client_port = data.get("port")
    sync_folder = data.get("sync_folder")

    clients = load_clients()
    clients[client_id] = {
        "ip": request.remote_addr,
        "port": client_port,
        "sync_folder": sync_folder
    }
    save_clients(clients)

    return jsonify({"message": "Client registered", "client_id": client_id})

@app.route("/update_metadata", methods=["POST"])
def update_metadata():
    data = request.json
    client_id = data.get("client_id")
    file_metadata = data.get("metadata")

    metadata = load_metadata()
    metadata[client_id] = file_metadata
    save_metadata(metadata)
    return jsonify({"message": "Metadata updated"})

@app.route("/delete_file", methods=["POST"])
def handle_file_deletion():
    data = request.json
    file_name = data.get("file_name")

    metadata = load_metadata()
    clients = load_clients()

    file_found = False

    for client, files in metadata.items():
        if file_name in files:
            file_found = True
            del metadata[client][file_name]

    if file_found:
        save_metadata(metadata)
        for client_id, client_info in clients.items():
            try:
                client_ip = client_info["ip"]
                client_port = client_info["port"]
                delete_url = f"http://{client_ip}:{client_port}/delete_file"
                requests.post(delete_url, json={"file_name": file_name}, timeout=5)
                print(f"Sent delete request to {client_id} at {client_ip}:{client_port}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to send delete request to {client_id}: {e}")
        return jsonify({"message": f"File {file_name} deleted across all clients."})

    return jsonify({"message": f"File {file_name} not found in metadata."})

@app.route("/sync", methods=["GET"])
def sync_files():
    metadata = load_metadata()
    clients = load_clients()
    sync_instructions = {}

    all_files = set()
    file_locations = {}

    for client_id, files in metadata.items():
        for file_name in files.keys():
            all_files.add(file_name)
            if file_name not in file_locations:
                file_locations[file_name] = []
            file_locations[file_name].append(client_id)

    for client_id, client_files in metadata.items():
        sync_instructions[client_id] = {"delete_files": []}

        for file_name in list(client_files.keys()):
            if file_name not in all_files:
                sync_instructions[client_id]["delete_files"].append(file_name)

        for file_name in all_files:
            if file_name not in client_files:
                versions = []
                for peer_id in file_locations[file_name]:
                    peer_info = metadata[peer_id][file_name]
                    versions.append({
                        "client_id": peer_id,
                        "hash": peer_info["hash"],
                        "last_modified": peer_info["last_modified"]
                    })
                latest = max(versions, key=lambda x: x["last_modified"])
                peer_info = clients[latest["client_id"]]
                if is_client_alive(peer_info["ip"], peer_info["port"]):
                    sync_instructions[client_id][file_name] = {
                        "ip": peer_info["ip"],
                        "port": peer_info["port"],
                        "sync_folder": peer_info["sync_folder"]
                    }
            else:
                local_hash = client_files[file_name]["hash"]
                all_hashes = {
                    peer: metadata[peer][file_name]["hash"]
                    for peer in file_locations[file_name]
                }
                if len(set(all_hashes.values())) > 1:
                    latest_peer = max(
                        file_locations[file_name],
                        key=lambda x: metadata[x][file_name]["last_modified"]
                    )
                    if client_id != latest_peer:
                        peer_info = clients[latest_peer]
                        if is_client_alive(peer_info["ip"], peer_info["port"]):
                            sync_instructions[client_id][file_name] = {
                                "ip": peer_info["ip"],
                                "port": peer_info["port"],
                                "sync_folder": peer_info["sync_folder"]
                            }

    return jsonify(sync_instructions)

@app.route("/get_clients", methods=["GET"])
def get_clients():
    requesting_ip = request.remote_addr
    clients = load_clients()
    filtered_clients = {
        client_id: details for client_id, details in clients.items()
        if details["ip"] != requesting_ip
    }
    return jsonify(filtered_clients)

# ------------------ Main ------------------

if __name__ == "__main__":
    initialize_files()
    app.run(host="0.0.0.0", port=5000, debug=True)
