import os
import json
import threading
import logging
from flask import Flask, render_template, jsonify, request, send_file
from werkzeug.utils import secure_filename
from main import run_automation, write_status
from src.config import INPUT_EXCEL_PATH, OUTPUT_EXCEL_PATH

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '.'

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

# Thread control variable
automation_thread = None
automation_lock = threading.Lock()

def check_status_file():
    if not os.path.exists("status.json"):
        write_status("Idle", 0, 0, 0, 0, 0)
    try:
        with open("status.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"status": "Idle", "processed": 0, "total": 0, "success": 0, "failed": 0, "skipped": 0}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status", methods=["GET"])
def get_status():
    status = check_status_file()
    # Check if thread is running to sync status
    is_alive = automation_thread is not None and automation_thread.is_alive()
    if not is_alive and status.get("status") == "Running":
        status["status"] = "Stopped"
    return jsonify(status)

@app.route("/api/records", methods=["GET"])
def get_records():
    target_path = OUTPUT_EXCEL_PATH if os.path.exists(OUTPUT_EXCEL_PATH) else INPUT_EXCEL_PATH
    if not os.path.exists(target_path):
        return jsonify([])
    try:
        from src.utils import read_excel_data
        df = read_excel_data(target_path)
        df = df.fillna("")
        records = df.to_dict(orient="records")
        return jsonify(records)
    except Exception as e:
        logger.error(f"Error reading records: {e}")
        return jsonify([])

@app.route("/api/start", methods=["POST"])
def start_automation():
    global automation_thread
    mode = request.args.get("mode", "all")
    with automation_lock:
        if automation_thread is not None and automation_thread.is_alive():
            return jsonify({"status": "error", "message": "Automation is already running."}), 400
        
        # Reset status file
        write_status("Running", 0, 0, 0, 0, 0)
        
        # Start background thread with mode argument
        automation_thread = threading.Thread(target=run_automation, args=(mode,), daemon=True)
        automation_thread.start()
        logger.info(f"Automation thread started via dashboard in mode: {mode}")
        return jsonify({"status": "success", "message": "Automation started."})

@app.route("/api/logs", methods=["GET"])
def get_logs():
    log_file = "automation.log"
    if not os.path.exists(log_file):
        return jsonify({"logs": "Log file not found yet."})
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            # Return last 50 lines
            return jsonify({"logs": "".join(lines[-50:])})
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}"})

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part in request."}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file."}), 400
    if file and file.filename.endswith(('.xlsx', '.xls')):
        filename = secure_filename(file.filename)
        # We save directly to the configured INPUT_EXCEL_PATH
        target_path = INPUT_EXCEL_PATH
        file.save(target_path)
        logger.info(f"New input excel file uploaded: {filename}")
        
        # Reset status
        write_status("Idle", 0, 0, 0, 0, 0)
        return jsonify({"status": "success", "message": f"Successfully uploaded {filename}"})
    return jsonify({"status": "error", "message": "Invalid file type. Please upload an Excel sheet."}), 400

@app.route("/api/download", methods=["GET"])
def download_processed_file():
    fmt = request.args.get("format", "xlsx").lower()
    csv_path = os.path.splitext(OUTPUT_EXCEL_PATH)[0] + ".csv"
    
    if fmt == "csv" and os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name=os.path.basename(csv_path))
    elif os.path.exists(OUTPUT_EXCEL_PATH):
        return send_file(OUTPUT_EXCEL_PATH, as_attachment=True, download_name=os.path.basename(OUTPUT_EXCEL_PATH))
    elif os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name=os.path.basename(csv_path))
    return jsonify({"status": "error", "message": "Processed file not found. Run automation first."}), 404

if __name__ == "__main__":
    # Serve locally on port 5000
    app.run(host="127.0.0.1", port=5000, debug=True)
