# 🔄 SyncIt

**SyncIt** is a lightweight, cross-platform file synchronization system built with Python and Flask. It enables automatic file syncing between multiple devices on the same network — just like Syncthing, but simplified.

---

## 🚀 Features

- 🔁 Sync files between multiple devices automatically.
- 🧠 Smart conflict resolution using file hash + last modified timestamp.
- ⚡ Sync only active clients using live socket ping.
- 🧩 Simple web interface for client setup.
- 📡 Server manages client metadata and file instructions.

---

## 🛠️ Setup Instructions

### 1. 🔁 Clone the Repository

```
git clone https://github.com/sdeamanraj/SyncIt.git
cd syncit
```
### 2. 📦 Install Dependencies
```
pip install -r requirements.txt
```
### 3. 🖥️ Running the Server
```
cd server
python server.py
```
Server will run on `http://<your-ip>:5000`

Tracks clients, sync metadata, and coordinates file transfers.

### 4. 💻 Running the Client
```
cd client
python c1.py
```
On first run, a web browser will open asking for:
✅ Client ID
✅ Folder path to sync

After that, syncing happens automatically.

---

### 🔒 Notes
- All communication happens over HTTP (local network).
- Designed for LAN usage — not secured for internet-facing deployment.
- Works on Windows, Linux, and Raspberry Pi.

### 📜 License
MIT License — feel free to fork, improve, and contribute!

### 🤝 Contributing
Pull requests are welcome. Let’s build a simpler, faster syncing alternative together!