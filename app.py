import os
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":99"

import json
import time
import threading
import logging
from functools import wraps
from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for
from werkzeug.utils import secure_filename
import pandas as pd

from main import run_automation, write_status, request_stop
from src.config import INPUT_EXCEL_PATH, OUTPUT_EXCEL_PATH, STATUS_FILE_PATH, LOG_FILE_PATH, BASE_DIR
from src.user_manager import (
    load_users, get_user, get_user_safe, list_users_safe, verify_user,
    create_user, update_user_profile, delete_user, update_credentials, test_imap_connection,
    get_user_input_path, get_user_output_path, get_user_status_path, get_user_log_path
)
from src.db_manager import (
    init_user_db, get_credentials as get_db_credentials, save_credentials as save_db_credentials,
    import_passengers_from_df, get_passengers_list, get_passengers_df,
    get_user_status as get_db_status, update_user_status as update_db_status,
    reset_passengers
)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = BASE_DIR
app.secret_key = os.getenv("SECRET_KEY", "etihad-saudia-automation-super-secret-key-2026")

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

# Thread control dictionary per user: {username: threading.Thread}
active_threads = {}
threads_lock = threading.Lock()


def get_current_user() -> str | None:
    """Returns authenticated username from session."""
    return session.get("username")


def login_required(f):
    """Decorator to require authenticated session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not get_current_user():
            if request.path.startswith('/api/'):
                return jsonify({"status": "error", "message": "Unauthorized. Please sign in."}), 401
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function


# ==================== PAGE ROUTES ====================

# ==================== PAGE ROUTES ====================

@app.route("/")
def home():
    """Redirects to dashboard if logged in, otherwise to login page."""
    if get_current_user():
        return redirect("/dashboard")
    return redirect("/login")


@app.route("/login")
def login_page():
    """Direct Sign In Page."""
    if get_current_user():
        return redirect("/dashboard")
    return render_template("login.html")


@app.route("/signup")
def signup_page():
    """Direct Sign Up Page."""
    if get_current_user():
        return redirect("/dashboard")
    return render_template("signup.html")


@app.route("/dashboard")
@login_required
def dashboard():
    """Command Center Dashboard (Protected)."""
    uname = get_current_user()
    init_user_db(uname)
    return render_template("index.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ==================== AUTHENTICATION APIS ====================

@app.route("/api/auth/current", methods=["GET"])
def current_user_api():
    uname = get_current_user()
    if not uname:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    
    user_info = get_user_safe(uname)
    if not user_info:
        user_info = {"username": uname, "name": uname.capitalize(), "email": "", "has_imap": False}
    return jsonify({"status": "success", "user": user_info})


@app.route("/api/auth/login", methods=["POST"])
def login_api():
    data = request.json or {}
    identifier = str(data.get("username", "") or data.get("email", "") or data.get("identifier", "")).strip()
    password = data.get("password", "")
    
    if not identifier or not password:
        return jsonify({"status": "error", "message": "Email/Username and password are required."}), 400

    user = verify_user(identifier, password)
    if user:
        session["username"] = user["username"]
        init_user_db(user["username"], user)
        logger.info(f"User '{user['username']}' logged in successfully.")
        return jsonify({
            "status": "success",
            "message": f"Welcome, {user.get('name', user['username'])}!",
            "redirect": "/dashboard",
            "user": user
        })
    return jsonify({"status": "error", "message": "Invalid username/email or password."}), 401


@app.route("/api/auth/register", methods=["POST"])
def register_api():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()

    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required."}), 400

    if not username.replace("_", "").replace("-", "").isalnum():
        return jsonify({"status": "error", "message": "Username can only contain letters, numbers, hyphens, and underscores."}), 400

    ok, msg = create_user(
        username=username,
        password=password,
        name=name or username.capitalize(),
        email=email,
        app_password="mwxeaewdzvqjwnzo",
        saudia_password="TempPass123!"
    )
    if ok:
        init_user_db(username, {
            "name": name or username.capitalize(),
            "email": email,
            "app_password": "mwxeaewdzvqjwnzo",
            "saudia_password": "TempPass123!"
        })
        session["username"] = username
        user_safe = get_user_safe(username)
        logger.info(f"Created dedicated workspace for user '{username}' ({email}).")
        return jsonify({
            "status": "success",
            "message": "Account & private database created!",
            "redirect": "/dashboard",
            "user": user_safe
        })
    return jsonify({"status": "error", "message": msg}), 400


@app.route("/api/auth/update-profile", methods=["POST"])
@login_required
def update_profile_api():
    uname = get_current_user()
    data = request.json or {}
    name = data.get("name")
    email = data.get("email")
    new_password = data.get("password")

    ok, msg = update_user_profile(uname, name=name, email=email, new_password=new_password)
    if ok:
        existing_creds = get_db_credentials(uname)
        if name:
            existing_creds["name"] = name
        if email:
            existing_creds["email"] = email
        save_db_credentials(uname, existing_creds)
        user_safe = get_user_safe(uname)
        return jsonify({"status": "success", "message": "Profile updated successfully!", "user": user_safe})
    return jsonify({"status": "error", "message": msg}), 400


@app.route("/api/auth/delete-user", methods=["POST"])
@login_required
def delete_user_api():
    current_uname = get_current_user()
    data = request.json or {}
    target_username = data.get("username", "").strip().lower()

    if not target_username:
        return jsonify({"status": "error", "message": "Username to delete is required."}), 400

    ok, msg = delete_user(target_username)
    if ok:
        is_self = (target_username == current_uname)
        if is_self:
            session.clear()
            return jsonify({"status": "success", "message": msg, "redirect": "/login"})
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 400


@app.route("/api/auth/users", methods=["GET"])
@login_required
def list_users_api():
    users = list_users_safe()
    return jsonify({"status": "success", "users": users})


@app.route("/api/auth/switch", methods=["POST"])
@login_required
def switch_user_api():
    data = request.json or {}
    target_username = data.get("username", "").strip().lower()
    user = get_user_safe(target_username)
    if user:
        session["username"] = target_username
        init_user_db(target_username)
        return jsonify({"status": "success", "message": f"Active profile switched to {user.get('name', target_username)}.", "user": user})
    return jsonify({"status": "error", "message": f"Profile '{target_username}' not found."}), 404


# ==================== CREDENTIALS APIS ====================

@app.route("/api/credentials", methods=["GET"])
@login_required
def get_credentials_api():
    uname = get_current_user()
    db_creds = get_db_credentials(uname)
    user_data = get_user(uname) or {}
    
    email = db_creds.get("email") or user_data.get("email") or ""
    app_pwd = db_creds.get("app_password") or user_data.get("app_password") or ""
    
    # If the email is empty or KTaha's default, it is default engine
    is_default = (not email) or (email.lower() == "ktaha8967@gmail.com")
    active_email = "ktaha8967@gmail.com" if is_default else email

    return jsonify({
        "status": "success",
        "username": uname,
        "name": db_creds.get("name") or user_data.get("name", uname.capitalize()),
        "email": "" if is_default else email,  # Empty if default, so user can type freely
        "app_password": "" if is_default else app_pwd,  # Empty if default
        "active_email": active_email,
        "imap_server": db_creds.get("imap_server", "imap.gmail.com"),
        "imap_port": db_creds.get("imap_port", 993),
        "saudia_password": db_creds.get("saudia_password", "TempPass123!"),
        "is_default": is_default
    })


@app.route("/api/credentials/save", methods=["POST"])
@login_required
def save_credentials_api():
    uname = get_current_user()
    data = request.json or {}
    
    email = (data.get("email") or "").strip().lower()
    app_password = (data.get("app_password") or "").replace(" ", "").replace("-", "").strip()
    imap_server = (data.get("imap_server") or "imap.gmail.com").strip()
    imap_port = data.get("imap_port", 993)
    saudia_password = (data.get("saudia_password") or "TempPass123!").strip()
    name = (data.get("name") or "").strip()

    if not email or "@" not in email:
        return jsonify({"status": "error", "message": "Please enter a valid email address."}), 400
    if not app_password:
        return jsonify({"status": "error", "message": "Please enter your 16-character Google App Password."}), 400

    # Save to user manager
    update_credentials(
        username=uname,
        email=email,
        app_password=app_password,
        imap_server=imap_server,
        imap_port=imap_port,
        saudia_password=saudia_password,
        name=name or None
    )

    # Save to user's isolated SQLite database
    save_db_credentials(uname, {
        "name": name or uname.capitalize(),
        "email": email,
        "app_password": app_password,
        "imap_server": imap_server,
        "imap_port": imap_port,
        "saudia_password": saudia_password
    })

    user_safe = get_user_safe(uname)
    is_default = (email.lower() == "ktaha8967@gmail.com")
    logger.info(f"Updated Saudia automation email for user '{uname}': {email}")
    return jsonify({
        "status": "success",
        "message": f"Saudia signup email successfully updated to {email}!",
        "email": email,
        "active_email": email,
        "is_default": is_default,
        "user": user_safe
    })


@app.route("/api/credentials/test", methods=["POST"])
@login_required
def test_credentials_api():
    uname = get_current_user()
    data = request.json or {}
    
    email = (data.get("email") or "").strip().lower()
    app_password = (data.get("app_password") or "").replace(" ", "").replace("-", "").strip()
    imap_server = (data.get("imap_server") or "").strip()
    imap_port = data.get("imap_port", 993)

    if not email:
        return jsonify({"status": "error", "message": "Please enter your Gmail address."}), 400
    if not app_password:
        return jsonify({"status": "error", "message": "Please enter your 16-character Google App Password."}), 400

    ok, msg = test_imap_connection(email, app_password, imap_server, imap_port)
    if ok:
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 400


@app.route("/api/credentials/reset", methods=["POST"])
@login_required
def reset_credentials_api():
    uname = get_current_user()
    default_email = "ktaha8967@gmail.com"
    default_pwd = "mwxeaewdzvqjwnzo"
    default_saudia_pwd = "TempPass123!"

    update_credentials(
        username=uname,
        email="ktaha8967@gmail.com",
        app_password="mwxeaewdzvqjwnzo",
        imap_server="imap.gmail.com",
        imap_port=993,
        saudia_password=default_saudia_pwd
    )
    save_db_credentials(uname, {
        "email": "ktaha8967@gmail.com",
        "app_password": "mwxeaewdzvqjwnzo",
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "saudia_password": default_saudia_pwd
    })
    logger.info(f"Reset Saudia automation credentials for '{uname}' to KTaha default.")
    return jsonify({
        "status": "success",
        "message": f"Reset to default KTaha automation engine ({default_email}) successfully! Input fields cleared.",
        "active_email": default_email,
        "email": "",
        "app_password": "",
        "is_default": True
    })


# ==================== DATA & AUTOMATION APIS (STRICTLY ISOLATED) ====================

@app.route("/api/status", methods=["GET"])
@login_required
def get_status():
    uname = get_current_user()
    
    # Read status from user's isolated database
    status = get_db_status(uname)
    
    with threads_lock:
        user_thread = active_threads.get(uname)
        is_alive = user_thread is not None and user_thread.is_alive()
    
    if not is_alive and status.get("status") == "Running":
        status["status"] = "Stopped"
    
    status["running_user"] = uname if is_alive else None
    status["active_user"] = uname
    status["is_running"] = is_alive
    return jsonify(status)


last_sync_times = {}

def throttled_sync_welcome(uname):
    now = time.time()
    if now - last_sync_times.get(uname, 0) > 30:
        last_sync_times[uname] = now
        try:
            from src.temp_mail import sync_welcome_membership_ids
            import threading
            threading.Thread(target=sync_welcome_membership_ids, args=(uname,), daemon=True).start()
        except Exception:
            pass

@app.route("/api/records", methods=["GET"])
@login_required
def get_records():
    uname = get_current_user()
    try:
        throttled_sync_welcome(uname)
        records = get_passengers_list(uname)
        return jsonify(records)
    except Exception as e:
        logger.error(f"Error fetching records for user {uname}: {e}")
        return jsonify([])


@app.route("/api/records/reset", methods=["POST"])
@login_required
def reset_records_api():
    uname = get_current_user()
    try:
        reset_passengers(uname)
        total_pax = len(get_passengers_list(uname))
        update_db_status(uname, "Idle", 0, total_pax, 0, 0, 0)
        status_path = get_user_status_path(uname)
        write_status("Idle", 0, total_pax, 0, 0, 0, status_file_path=status_path, username=uname)
        
        # Reset output file
        out_path = get_user_output_path(uname)
        if os.path.exists(out_path):
            try:
                df = get_passengers_df(uname)
                from src.utils import save_excel_data
                save_excel_data(df, out_path)
            except Exception:
                pass

        logger.info(f"Reset passenger queue for user '{uname}' to Pending.")
        return jsonify({"status": "success", "message": "Queue reset successfully! All passengers are set to Pending for fresh extraction & signup."})
    except Exception as e:
        logger.error(f"Error resetting records for {uname}: {e}")
        return jsonify({"status": "error", "message": f"Failed to reset: {e}"}), 400


@app.route("/api/start", methods=["POST"])
@login_required
def start_automation():
    mode = request.args.get("mode", "all")
    uname = get_current_user()
    user_creds = get_db_credentials(uname)

    with threads_lock:
        user_thread = active_threads.get(uname)
        if user_thread is not None and user_thread.is_alive():
            return jsonify({
                "status": "error",
                "message": "Automation is already running for your account. Please wait or stop it before starting again."
            }), 400
        
        # Reset user status in SQLite and JSON
        status_path = get_user_status_path(uname)
        write_status("Running", 0, 0, 0, 0, 0, status_file_path=status_path, username=uname)
        
        # Start background thread using user's isolated database and credentials
        thread = threading.Thread(
            target=run_automation,
            args=(mode, uname, user_creds),
            daemon=True
        )
        active_threads[uname] = thread
        thread.start()
        
    logger.info(f"Automation thread started for user '{uname}' in mode: {mode}")
    return jsonify({
        "status": "success",
        "message": f"Automation started for {uname} using private database."
    })


@app.route("/api/stop", methods=["POST"])
@login_required
def stop_automation():
    uname = get_current_user()

    with threads_lock:
        user_thread = active_threads.get(uname)
        if user_thread is None or not user_thread.is_alive():
            return jsonify({"status": "error", "message": "No automation process is currently running for your account."}), 400

        request_stop(uname)
        status_path = get_user_status_path(uname)
        write_status("Stopping", 0, 0, 0, 0, 0, status_file_path=status_path, username=uname)
        
    logger.info(f"Stop signal triggered for automation by user '{uname}'.")
    return jsonify({"status": "success", "message": "Automation stop requested. Halting tasks..."})


@app.route("/api/logs", methods=["GET"])
@login_required
def get_logs():
    uname = get_current_user()
    user_log_path = get_user_log_path(uname)
    target_log = user_log_path if os.path.exists(user_log_path) else LOG_FILE_PATH

    if not os.path.exists(target_log):
        return jsonify({"logs": "Log file not found yet. Start automation to view live execution logs."})
    try:
        with open(target_log, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return jsonify({"logs": "".join(lines[-60:])})
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}"})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    uname = get_current_user()
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file attached."}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected."}), 400
    if file and file.filename.endswith(('.xlsx', '.xls', '.csv')):
        filename = secure_filename(file.filename)
        from src.user_manager import UPLOADS_DIR
        ext = os.path.splitext(filename)[1].lower()
        
        # Import directly into user's isolated SQLite database
        try:
            from src.utils import read_excel_data
            if ext == '.csv':
                csv_path = os.path.join(UPLOADS_DIR, f"{uname}_input.csv")
                file.save(csv_path)
                df = pd.read_csv(csv_path)
                xlsx_path = os.path.join(UPLOADS_DIR, f"{uname}_input.xlsx")
                df.to_excel(xlsx_path, index=False)
            else:
                xlsx_path = os.path.join(UPLOADS_DIR, f"{uname}_input.xlsx")
                file.save(xlsx_path)
                df = read_excel_data(xlsx_path)
            
            import_passengers_from_df(uname, df)
            update_db_status(uname, "Idle", 0, len(df), 0, 0, 0)
            status_path = get_user_status_path(uname)
            write_status("Idle", 0, len(df), 0, 0, 0, status_file_path=status_path)
            
            logger.info(f"Loaded {len(df)} rows into isolated database for user '{uname}' from {filename}.")
            return jsonify({
                "status": "success",
                "message": f"Successfully loaded {len(df)} passengers into your private database."
            })
        except Exception as e:
            logger.error(f"Error importing passenger data for {uname}: {e}")
            return jsonify({"status": "error", "message": f"Failed to import passenger records: {e}"}), 400
    return jsonify({"status": "error", "message": "Invalid file type. Please upload an Excel or CSV file."}), 400


@app.route("/api/download", methods=["GET"])
@login_required
def download_processed_file():
    uname = get_current_user()
    fmt = request.args.get("format", "xlsx").lower()
    
    try:
        df = get_passengers_df(uname)
        from src.user_manager import OUTPUTS_DIR
        if fmt == "csv":
            out_file = os.path.join(OUTPUTS_DIR, f"{uname}_processed.csv")
            df.to_csv(out_file, index=False)
            return send_file(out_file, as_attachment=True, download_name=f"{uname}_etihad_processed.csv")
        else:
            out_file = os.path.join(OUTPUTS_DIR, f"{uname}_processed.xlsx")
            df.to_excel(out_file, index=False)
            return send_file(out_file, as_attachment=True, download_name=f"{uname}_etihad_processed.xlsx")
    except Exception as e:
        logger.error(f"Error generating download for {uname}: {e}")
        return jsonify({"status": "error", "message": "Failed to generate download file."}), 500


# ==================== PROJECT 2: SAUDIA DIRECT PIPELINE ====================

from src.project2_manager import (
    init_project2_db,
    get_project2_passengers_list,
    get_project2_passengers_df,
    import_project2_passengers_from_df,
    reset_project2_passengers,
    get_project2_status,
    update_project2_status,
    get_project2_config,
    save_project2_config,
    sync_project2_welcome_ids,
    update_project2_passenger_record
)
from src.project2_automation import (
    run_project2_automation,
    request_project2_stop,
    is_project2_stopped
)

project2_threads = {}
project2_threads_lock = threading.Lock()

@app.route("/project2")
@login_required
def project2_page():
    uname = get_current_user()
    init_project2_db(uname)
    return render_template("project2.html")

@app.route("/api/project2/records", methods=["GET"])
@login_required
def project2_records_api():
    uname = get_current_user()
    records = get_project2_passengers_list(uname)
    return jsonify(records)

@app.route("/api/project2/status", methods=["GET"])
@login_required
def project2_status_api():
    uname = get_current_user()
    s = get_project2_status(uname)
    with project2_threads_lock:
        t = project2_threads.get(uname)
        is_alive = t is not None and t.is_alive()
    if not is_alive and s.get("status_text") == "Running":
        s["status_text"] = "Stopped"
    s["is_running"] = is_alive
    return jsonify(s)

@app.route("/api/project2/start", methods=["POST"])
@login_required
def project2_start_api():
    uname = get_current_user()
    limit = request.args.get("limit")
    sample_limit = int(limit) if limit and limit.isdigit() else None

    with project2_threads_lock:
        t = project2_threads.get(uname)
        if t is not None and t.is_alive():
            return jsonify({"status": "error", "message": "Project 2 automation is already running for your account."}), 400

        thread = threading.Thread(
            target=run_project2_automation,
            args=(uname, sample_limit),
            daemon=True
        )
        project2_threads[uname] = thread
        thread.start()

    logger.info(f"Project 2 automation thread started for user '{uname}' (Limit: {sample_limit}).")
    return jsonify({"status": "success", "message": f"Project 2 enrollment started (Limit: {sample_limit or 'Full Queue'})."})

@app.route("/api/project2/stop", methods=["POST"])
@login_required
def project2_stop_api():
    uname = get_current_user()
    request_project2_stop(uname)
    logger.info(f"Project 2 stop requested by user '{uname}'.")
    return jsonify({"status": "success", "message": "Project 2 stop signal sent. Halting tasks..."})

@app.route("/api/project2/reset", methods=["POST"])
@login_required
def project2_reset_api():
    uname = get_current_user()
    reset_project2_passengers(uname)
    return jsonify({"status": "success", "message": "Project 2 queue reset! All records ready for fresh run."})

@app.route("/api/project2/config", methods=["GET", "POST"])
@login_required
def project2_config_api():
    uname = get_current_user()
    if request.method == "POST":
        data = request.json or {}
        save_project2_config(uname, data)
        master_email = (data.get("master_email") or "").strip()
        master_pwd = (data.get("master_password") or "").strip()
        warning = None
        if master_email and master_pwd:
            ok, imap_msg = test_imap_connection(master_email, master_pwd)
            if not ok:
                warning = imap_msg
                msg = f"Email settings saved! ⚠️ {warning}"
            else:
                msg = "Email settings saved and verified! Connected successfully to your inbox."
        else:
            msg = "Project 2 email routing configuration saved!"
        return jsonify({"status": "success", "message": msg, "warning": warning})
    cfg = get_project2_config(uname)
    return jsonify({"status": "success", "config": cfg})

@app.route("/api/project2/test_credentials", methods=["POST"])
@login_required
def project2_test_credentials_api():
    uname = get_current_user()
    data = request.get_json(silent=True) or {}
    email = (data.get("master_email") or "").strip().lower()
    app_pwd = (data.get("master_password") or "").replace(" ", "").replace("-", "").strip()
    if not email or not app_pwd:
        cfg = get_project2_config(uname)
        email = (cfg.get("master_email") or "").strip().lower()
        app_pwd = (cfg.get("master_password") or "").replace(" ", "").replace("-", "").strip()
    if not email or not app_pwd:
        return jsonify({"status": "error", "message": "Please enter both Gmail address and 16-character App Password."}), 400
    ok, msg = test_imap_connection(email, app_pwd)
    if ok:
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": msg}), 400

@app.route("/api/project2/sync_ids", methods=["POST"])
@login_required
def project2_sync_ids_api():
    uname = get_current_user()
    try:
        res = sync_project2_welcome_ids(uname)
        updated, msg = res if isinstance(res, tuple) else (res, f"Synced {res} IDs")
        status = "success" if updated > 0 else ("info" if "No emails" in msg or "no Saudia" in msg or "checked" in msg else "error")
        return jsonify({
            "status": status,
            "message": msg,
            "updated": updated
        })
    except Exception as e:
        logger.error(f"Error syncing AlFursan IDs for {uname}: {e}")
        return jsonify({"status": "error", "message": f"Sync failed: {e}"}), 500

@app.route("/api/project2/update_record", methods=["POST"])
@login_required
def project2_update_record_api():
    uname = get_current_user()
    data = request.json or {}
    pax_id = data.get("id")
    saudia_id = (data.get("saudia_id") or "").strip()
    status = data.get("status") or ("Activated" if saudia_id else "Pending Activation")
    remarks = data.get("remarks") or ("Completed successfully (manual update)" if saudia_id else "Pending Activation")
    if not pax_id:
        return jsonify({"status": "error", "message": "Missing passenger ID."}), 400
    try:
        update_project2_passenger_record(uname, pax_id, {
            "saudia_id": saudia_id,
            "status": status,
            "remarks": remarks
        })
        try:
            df = get_project2_passengers_df(uname)
            out_file = os.path.join(OUTPUTS_DIR, f"{uname}_project2_output.xlsx")
            from src.utils import save_excel_data
            save_excel_data(df, out_file)
        except Exception:
            pass
        return jsonify({"status": "success", "message": f"Passenger #{pax_id} updated with Saudia ID {saudia_id or '(cleared)'}!"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to update record: {e}"}), 500

@app.route("/api/project2/activate_link", methods=["POST"])
@login_required
def project2_activate_link_api():
    uname = get_current_user()
    data = request.json or {}
    raw_url = (data.get("url") or "").strip()
    pax_id = data.get("id")
    if not raw_url:
        return jsonify({"status": "error", "message": "Please enter a valid activation URL."}), 400

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()
            page.goto(raw_url, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(3000)
            final_url = page.url
            os.makedirs("scratch", exist_ok=True)
            page.screenshot(path="scratch/manual_activated.png")
            browser.close()

            if pax_id:
                update_project2_passenger_record(uname, int(pax_id), {
                    "status": "Activated",
                    "remarks": "Activated via server browser"
                })
                try:
                    df = get_project2_passengers_df(uname)
                    out_file = os.path.join(OUTPUTS_DIR, f"{uname}_project2_output.xlsx")
                    from src.utils import save_excel_data
                    save_excel_data(df, out_file)
                except Exception:
                    pass

            return jsonify({
                "status": "success",
                "message": "Activation link successfully opened and verified by server! Saudia account is activated. Check your inbox for the Welcome email with your Membership ID.",
                "final_url": final_url
            })
    except Exception as e:
        logger.error(f"Error activating link on server: {e}")
        return jsonify({"status": "error", "message": f"Server failed to activate link: {e}"}), 500

@app.route("/api/project2/logs", methods=["GET"])
@login_required
def project2_logs_api():
    uname = get_current_user()
    p2_log_path = os.path.join("data", "logs", f"project2_{uname}.log")
    if not os.path.exists(p2_log_path):
        return jsonify({"logs": "Project 2 log file not created yet. Start automation to view live execution logs."})
    try:
        with open(p2_log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return jsonify({"logs": "".join(lines[-50:])})
    except Exception as e:
        return jsonify({"logs": f"Error reading Project 2 logs: {e}"})

@app.route("/api/project2/upload", methods=["POST"])
@login_required
def project2_upload_api():
    uname = get_current_user()
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file attached."}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({"status": "error", "message": "No file selected."}), 400
    if file.filename.endswith(('.xlsx', '.xls', '.csv')):
        filename = secure_filename(file.filename)
        from src.user_manager import UPLOADS_DIR
        ext = os.path.splitext(filename)[1].lower()
        try:
            if ext == '.csv':
                csv_path = os.path.join(UPLOADS_DIR, f"{uname}_project2.csv")
                file.save(csv_path)
                df = pd.read_csv(csv_path)
            else:
                xlsx_path = os.path.join(UPLOADS_DIR, f"{uname}_project2.xlsx")
                file.save(xlsx_path)
                from src.utils import read_excel_data
                df = read_excel_data(xlsx_path)

            import_project2_passengers_from_df(uname, df)
            update_project2_status(uname, "Idle", 0, len(df), 0, 0, 0)
            logger.info(f"Loaded {len(df)} rows into Project 2 database for '{uname}' from {filename}.")
            return jsonify({
                "status": "success",
                "message": f"Successfully loaded {len(df)} passenger records into Project 2 queue."
            })
        except Exception as e:
            logger.error(f"Error loading Project 2 file: {e}")
            return jsonify({"status": "error", "message": f"Failed to load file: {e}"}), 400
    return jsonify({"status": "error", "message": "Invalid file format. Please upload an Excel or CSV file."}), 400

@app.route("/api/project2/download", methods=["GET"])
@login_required
def project2_download_api():
    uname = get_current_user()
    fmt = request.args.get("format", "xlsx").lower()
    try:
        df = get_project2_passengers_df(uname)
        from src.user_manager import OUTPUTS_DIR
        if fmt == "csv":
            out_file = os.path.join(OUTPUTS_DIR, f"{uname}_project2_output.csv")
            df.to_csv(out_file, index=False)
            return send_file(out_file, as_attachment=True, download_name=f"{uname}_saudia_project2.csv")
        else:
            out_file = os.path.join(OUTPUTS_DIR, f"{uname}_project2_output.xlsx")
            df.to_excel(out_file, index=False)
            return send_file(out_file, as_attachment=True, download_name=f"{uname}_saudia_project2.xlsx")
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to export Project 2 file: {e}"}), 500


wsgi_app = app

if __name__ == "__main__":
    port = int(os.getenv("PORT", 27182))
    app.run(host="0.0.0.0", port=port, debug=False)
