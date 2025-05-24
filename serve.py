# @app.websocket("/ws/{client_id}")
# async def websocket_endpoint(websocket: WebSocket, client_id: int):
#     await manager.connect(websocket)
#     try:
#         while True:
#             data = await websocket.receive_text()
#             await manager.send_personal_message(f"You wrote: {data}", websocket)
#             await manager.broadcast(f"Client #{client_id} says: {data}")
#     except WebSocketDisconnect:
#         manager.disconnect(websocket)
#         await manager.broadcast(f"Client #{client_id} left the chat")

import requests
import base64
import os
import tempfile
import subprocess
import signal
import threading
import uuid
from flask import Flask, request, send_file
from flask_socketio import SocketIO, emit
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Store active processes and their information
active_sessions = {}

@app.route('/')
def index():
    return app.send_static_file('index.html')

@socketio.on('connect')
def handle_connect():
    session_id = str(uuid.uuid4())
    active_sessions[request.sid] = {
        'session_id': session_id,
        'process': None,
        'temp_dir': None,
        'output_file': None
    }
    emit('session_created', {'session_id': session_id})

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_sessions:
        session = active_sessions[request.sid]
        if session['process']:
            try:
                session['process'].terminate()
                session['process'].wait(timeout=2)
            except:
                if session['process']:
                    try:
                        session['process'].kill()
                    except:
                        pass
        
        # Clean up temp directory
        if session['temp_dir'] and os.path.exists(session['temp_dir']):
            try:
                for file in os.listdir(session['temp_dir']):
                    os.remove(os.path.join(session['temp_dir'], file))
                os.rmdir(session['temp_dir'])
            except:
                pass
        
        del active_sessions[request.sid]

@socketio.on('ping')
def handle_ping():
    emit("pong", "pong")

@app.route('/upload', methods=['POST'])
def handle_upload():
    if 'file' not in request.files:
        return {'error': 'No file provided'}, 400
    
    file = request.files['file']
    if not file.filename.endswith('.epub'):
        return {'error': 'Only EPUB files are allowed'}, 400
    
    # Create temporary directory and file
    temp_dir = tempfile.mkdtemp()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.epub', dir=temp_dir)
    file.save(temp_file.name)
    
    # Store file info in active_sessions with temp file as key
    active_sessions[os.path.basename(temp_file.name)] = {
        'temp_dir': temp_dir,
        'file_path': temp_file.name,
        'original_name': file.filename
    }
    
    return {
        'message': 'File uploaded successfully',
        'file_id': os.path.basename(temp_file.name)
    }, 200

@socketio.on('start_processing')
def start_processing(data):
    if 'file_id' not in data:
        emit('error', {'message': 'No file ID provided'})
        return
    
    file_id = data['file_id']
    if file_id not in active_sessions:
        emit('error', {'message': 'File not found'})
        return
    
    session = active_sessions[file_id]
    
    # Find uploaded EPUB file
    epub_files = [f for f in os.listdir(session['temp_dir']) if f.endswith('.epub')]
    if not epub_files:
        emit('error', {'message': 'No EPUB file found'})
        return
    
    file_path = os.path.join(session['temp_dir'], epub_files[0])
    output_file = os.path.join(session['temp_dir'], 'summary.txt')
    session['output_file'] = output_file
    
    # Store socket ID for background thread
    session['socket_id'] = request.sid
    
    # Set up command
    cmd = [
        'uv', 'run', 'python', 'sums.py',
        '--path', file_path,
        '--output', output_file,
        '--skip', str(data.get('skip', 0))
    ]
    
    # Start process and capture output
    def process_output():
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            shell=False,
        )
        
        session['process'] = process
        
        for line in iter(process.stdout.readline, ''):
            if not line:
                break
            socketio.emit('update', {'line': line}, room=session['socket_id'])
        
        process.wait()
        
        if process.returncode in (0, 1):
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    summary = f.read()
                socketio.emit('complete', {'status': 'complete', 'summary': summary}, room=session['socket_id'])
            else:
                socketio.emit('error', {'message': 'Output file not found'}, room=session['socket_id'])
        else:
            socketio.emit('error', {'message': f'Process failed with code {process.returncode}'}, room=session['socket_id'])
    
    threading.Thread(target=process_output).start()
    emit('processing_started', {'message': 'Processing started'})

@socketio.on('stop_processing')
def stop_processing(data):
    file_id = data['file_id']
    
    if file_id not in active_sessions:
        emit('error', {'message': 'No session found'})
        return
    
    session = active_sessions[file_id]
        
    if session['process']:
        try:
            session['process'].terminate()
            # session['process'].send_signal(signal.SIGINT)
            # os.kill(session['process'].pid, signal.SIGINT)
            # os.killpg(os.getpgid(session['process'].pid), signal.SIGINT)
            session['process'].wait(timeout=5)
            
            # Check if output file exists and send it
            if session['output_file'] and os.path.exists(session['output_file']):
                with open(session['output_file'], 'r') as f:
                    summary = f.read()
                emit('complete', {'status': 'interrupted', 'summary': summary})
            else:
                emit('complete', {'status': 'interrupted', 'summary': 'No output generated yet'})
        except:
            emit('error', {'message': 'Failed to stop processing'})
            if session['process']:
                try:
                    session['process'].kill()
                except:
                    raise RuntimeError("Failed to stop process")
    else:
        emit('error', {'message': 'No active process to stop'})

@socketio.on('download_summary')
def download_summary():
    if request.sid not in active_sessions:
        emit('error', {'message': 'No session found'})
        return
    
    session = active_sessions[request.sid]
    
    if session['output_file'] and os.path.exists(session['output_file']):
        emit('download_ready', {'url': f'/download/{session["session_id"]}'})
    else:
        emit('error', {'message': 'No summary file available'})

@app.route('/download/<session_id>')
def download(session_id):
    # Find the session by ID
    session = None
    for sid, data in active_sessions.items():
        if data['session_id'] == session_id:
            session = data
            break
    
    if not session or not session['output_file'] or not os.path.exists(session['output_file']):
        return "File not found", 404
    
    return send_file(session['output_file'], as_attachment=True, download_name="summary.txt")

@app.route('/share', methods=['POST'])
def share_summary():
    try:
        data = request.get_json()
        if not data or 'content' not in data:
            return {'error': 'No content provided'}, 400
        
        content = data['content']
        url = upload_to_0x0st(content)
        return {'url': url}
    except Exception as e:
        return {'error': str(e)}, 500

def upload_to_0x0st(content, filename="document.md"):
    """Upload markdown content to 0x0.st"""
    print(f"Uploading to 0x0.st...")
    
    try:
        url = "https://0x0.st"
        
        # Send the content directly as a file
        files = {'file': (filename, content.encode('utf-8'), 'text/markdown')}
        headers = {'User-Agent': 'curl/7.68.0'}  # Some services check user agent
        response = requests.post(url, files=files, headers=headers)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        print(f"Upload failed: {e}")
        raise

if __name__ == '__main__':
    socketio.run(app, debug=True)



# usage: sums.py [-h] [--source SOURCE] [--path PATH] [--skip SKIP] [--output OUTPUT]

# Summarize an EPUB book using Gemini

# options:
#   -h, --help       show this help message and exit
#   --source SOURCE  Directory to search for EPUB files
#   --path PATH      Direct path to EPUB file
#   --skip SKIP      Number of chapters to skip
#   --output OUTPUT  Output file path