from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from dotenv import load_dotenv
import os
import uvicorn
import bcrypt
import hashlib
import secrets
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi_socketio import SocketManager

# Load environment variables explicitly
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
print(f"DEBUG: OTP_DEV_MODE is {os.environ.get('OTP_DEV_MODE')}")

import cloudinary
import cloudinary.uploader
import cloudinary.utils
import time

# Configure Cloudinary
cloudinary.config(
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key = os.environ.get('CLOUDINARY_API_KEY'),
    api_secret = os.environ.get('CLOUDINARY_API_SECRET'),
    secure = True
)

# Supabase Config
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Global HTTP Client for connection pooling
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=10.0)
    yield
    await http_client.aclose()

app = FastAPI(lifespan=lifespan)
sm = SocketManager(app=app, mount_location='/socket.io')
sio = sm._sio

@sio.on('join-room')
async def handle_join_room(sid, data):
    room_id = data.get('roomId')
    if room_id:
        sio.enter_room(sid, room_id)
        print(f"User joined: {room_id}")

@sio.on('join')
async def handle_join(sid, data):
    user_id = data.get('user_id') # Personal notification room
    if user_id:
        sio.enter_room(sid, str(user_id))
    
    # Check for admin room join
    if data.get('is_admin') is True:
        sio.enter_room(sid, 'admin_room')
        print(f"Admin joined: {sid}")

async def broadcast_event(room: str, event_type: str, data: dict = None):
    """Universal event dispatcher for real-time synchronization."""
    await sio.emit('system_event', {
        'type': event_type,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'data': data or {}
    }, room=room)

async def notify_admin(event_type: str, data: dict = None):
    """Broadcast an update to all connected admins."""
    await broadcast_event('admin_room', event_type, data)

@sio.on('leave')
async def handle_leave(sid, data):
    room_id = data.get('roomId')
    if room_id:
        sio.leave_room(sid, room_id)

@sio.on('send-message')
async def handle_send_message(sid, data):
    room_id = data.get('roomId')
    message = data.get('message', '').strip()
    sender_id = data.get('senderId')
    sender_type = data.get('senderType')
    
    if room_id and message:
        print(f"Message received: {data}")
        # Broadcast to all in the room
        await sio.emit('receive-message', {
            'roomId': room_id,
            'sender_id': sender_id,
            'sender_type': sender_type,
            'message': message,
            'created_at': "now()" # Placeholder or iso format
        }, room=room_id)

@sio.on('typing')
async def handle_typing(sid, data):
    room_id = data.get('roomId')
    is_typing = data.get('typing', False)
    if room_id:
        await sio.emit('typing-status', {
            'typing': is_typing, 
            'roomId': room_id
        }, room=room_id, skip_sid=sid)

@sio.on('delete-message')
async def handle_delete_message(sid, data):
    room_id = data.get('roomId')
    message_id = data.get('messageId')
    if room_id and message_id:
        await sio.emit('message-deleted', {
            'roomId': room_id,
            'messageId': message_id
        }, room=room_id)

# Add Middlewares
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY", "fallback-secret-key-123"))
app.add_middleware(GZipMiddleware, minimum_size=1000) # Compress responses > 1KB

async def supabase_request(method: str, endpoint: str, data: dict = None, params: dict = None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    try:
        response = await http_client.request(method, url, headers=SUPABASE_HEADERS, json=data, params=params)
        
        if response.status_code >= 400:
            print(f"Supabase Error [{response.status_code}]: {response.text}")
            raise HTTPException(
                status_code=response.status_code, 
                detail=f"Database operational error: {response.text}"
            )
        return response
    except httpx.RequestError as e:
        print(f"Connection Error: {str(e)}")
        raise HTTPException(status_code=503, detail="Database service temporarily unavailable")
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        print(f"Internal Error: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal server error occurred")

# Static & Templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.cache = None # Fix: Disable Jinja2 cache for Vercel production stability

# Middleware for Cache Control (Zero-Glitch Delivery)
@app.middleware("http")
async def add_cache_control_header(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    
    # Static Assets: Intensive caching for immutable types
    if path.startswith("/static"):
        if any(path.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".mp4", ".svg", ".css", ".js"]):
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=3600"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
    else:
        # Dynamic Content: No cache to ensure data freshness
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    
    return response

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/worker-count")
async def get_worker_count(lat: float = None, lng: float = None):
    """
    Lightweight endpoint: returns count of active verified workers.
    Optionally filters by ~10km bounding box if lat/lng provided.
    Designed to be non-blocking with a safe fallback.
    """
    FALLBACK_COUNT = 20
    try:
        # Use Supabase count header for efficiency (no row data fetched)
        count_headers = {
            **SUPABASE_HEADERS,
            "Prefer": "count=exact"
        }
        query = "workers?verification_status=eq.VERIFIED&subscription_status=neq.EXPIRED&select=id"

        if lat is not None and lng is not None:
            lat_delta = 0.1
            lng_delta = 0.1
            query += f"&latitude=gte.{lat - lat_delta}&latitude=lte.{lat + lat_delta}"
            query += f"&longitude=gte.{lng - lng_delta}&longitude=lte.{lng + lng_delta}"

        url = f"{SUPABASE_URL}/rest/v1/{query}"
        response = await http_client.get(url, headers=count_headers)

        if response.status_code == 200:
            # Supabase returns count in Content-Range header: e.g., "0-24/25"
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range:
                total = int(content_range.split("/")[1])
                return JSONResponse({"count": total})
            # Fallback: count from response body
            workers = response.json()
            return JSONResponse({"count": len(workers)})

        return JSONResponse({"count": FALLBACK_COUNT})
    except Exception as e:
        print(f"[worker-count] Non-critical error: {e}")
        return JSONResponse({"count": FALLBACK_COUNT})

@app.get("/api/workers")
async def get_workers(category: str = "All", lat: float = None, lng: float = None):
    # Master Optimization: Fetch ONLY minimum required fields for map rendering
    fields = "id,name,phone,service,experience,area,latitude,longitude,is_online,last_active,profile_photo,verification_status,subscription_status,ambassadors(name)"
    query = f"workers?verification_status=eq.VERIFIED&subscription_status=neq.EXPIRED&latitude=not.is.null&longitude=not.is.null&select={fields}"
    
    # 1. Category Filtering
    if category != "All":
        query += f"&service=eq.{category}"
        
    # 2. Geospatial Filtering (10km Bounding Box)
    if lat is not None and lng is not None:
        # ~10km bounding box (approximate)
        # 1 deg lat is ~111km, 1 deg lng at 12 deg lat is ~108km
        lat_delta = 0.1 # approx 11km
        lng_delta = 0.1 # approx 11km
        
        lat_min = lat - lat_delta
        lat_max = lat + lat_delta
        lng_min = lng - lng_delta
        lng_max = lng + lng_delta
        
        query += f"&latitude=gte.{lat_min}&latitude=lte.{lat_max}"
        query += f"&longitude=gte.{lng_min}&longitude=lte.{lng_max}"
        
    response = await supabase_request("GET", query)
    workers = response.json()
    
    # Debug log
    if lat is not None:
        print(f"DEBUG: Found {len(workers)} workers for category {category} near {lat}, {lng}")
    else:
        print(f"DEBUG: Found {len(workers)} workers for category {category} (Global)")
    
    return workers


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, category: str = "All"):
    user_id    = request.session.get("user_id", "")
    user_phone = request.session.get("user_phone", "")
    return templates.TemplateResponse(request=request, name="map.html", context={
        "category": category,
        "user_id": user_id,
        "user_phone": user_phone
    })


@app.get("/user/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/user/login")
    return templates.TemplateResponse(request=request, name="user_dashboard.html")

@app.get("/api/user/logout")
async def user_logout(request: Request):
    request.session.pop("user_id", None)
    request.session.pop("user_phone", None)
    return RedirectResponse(url="/")

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if request.session.get("worker_id"):
        return RedirectResponse(url="/worker/dashboard")
    return templates.TemplateResponse(request=request, name="register.html")

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, worker_id: str = None):
    # Verify page requires a worker_id in the query parameter to map the uploaded photos to the correct worker
    if not worker_id:
        return RedirectResponse(url="/register")
    return templates.TemplateResponse(request=request, name="verify.html", context={"worker_id": worker_id})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    is_logged_in = request.session.get("admin_logged_in", False)
    return templates.TemplateResponse(request=request, name="admin.html", context={"is_logged_in": is_logged_in})

@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request=request, name="about.html")

@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse(request=request, name="contact.html")

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request=request, name="terms.html")


# ── IN-APP CHAT & JOB REQUEST SYSTEM ──────────────────────────────────────

@app.post("/api/messages/send")
async def send_message(request: Request):
    try:
        body = await request.json()
        chat_id     = body.get("chat_id", "") or body.get("roomId", "")
        sender_id   = body.get("sender_id", "") or body.get("senderId", "")
        sender_type = body.get("sender_type", "")  # 'user' or 'worker'
        message     = body.get("message", "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    if not all([chat_id, sender_id, sender_type, message]):
        raise HTTPException(status_code=422, detail="Missing required fields.")

    # Persist to database
    res = await supabase_request("POST", "messages", data={
        "chat_id": chat_id,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "message": message
    })
    if not res.is_success:
        raise HTTPException(status_code=500, detail="Message persistence failed.")
        
    res_json = res.json()
    msg_data = res_json[0] if isinstance(res_json, list) else res_json

    # Real-time Broadcast to the Room
    await broadcast_event(chat_id, "receive-message", {
        "roomId": chat_id,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "message": message,
        "created_at": msg_data.get("created_at")
    })

    # Real-time Notification to Recipient (for badges)
    recipient_id = chat_id.split("_worker_")[1] if sender_type == "user" else chat_id.split("_worker_")[0].replace("user_", "")
    await broadcast_event(str(recipient_id), "notify_message", {"roomId": chat_id, "message": message})

    return JSONResponse({"status": "success", "data": msg_data})


@app.get("/api/messages/{chat_id}")
async def get_messages(chat_id: str):
    res = await supabase_request("GET", f"messages?chat_id=eq.{chat_id}&order=created_at.asc&limit=200")
    return JSONResponse({"status": "success", "messages": res.json()})


@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: str):
    res = await supabase_request("DELETE", f"messages?id=eq.{message_id}")
    return JSONResponse({"status": "success", "message": "Deleted successfully"})


def mask_phone(ph: str) -> str:
    if not ph: return "User"
    clean = "".join(filter(str.isdigit, ph))
    if len(clean) < 2: return "User"
    return "********" + clean[-2:]


@app.get("/api/chats/worker/{worker_id}")
async def get_worker_chats(worker_id: str):
    """Returns all unique chats for a worker, with last message and user info."""
    res = await supabase_request(
        "GET", f"messages?chat_id=like.*_worker_{worker_id}*&order=created_at.desc&limit=500"
    )
    messages = res.json()

    seen = {}
    for m in messages:
        cid = m["chat_id"]
        if cid not in seen:
            seen[cid] = m
    
    chats = list(seen.values())
    
    # Enrichment: User Phone Numbers
    user_ids = list(set([cid.split("_worker_")[0].replace("user_", "") for cid in seen.keys() if "user_" in cid]))
    if user_ids:
        # Batch fetch user phones
        ids_str = ",".join([f"{uid}" for uid in user_ids])
        u_res = await supabase_request("GET", f"users?id=in.({ids_str})&select=id,phone")
        users_map = {u["id"]: u["phone"] for u in u_res.json()}
        for chat in chats:
            uid = chat["chat_id"].split("_worker_")[0].replace("user_", "")
            raw_phone = users_map.get(uid, "Unknown User")
            chat["user_phone"] = mask_phone(raw_phone)
            
    return JSONResponse({"status": "success", "chats": chats})


@app.get("/api/chats/user/{user_id}")
async def get_user_chats(user_id: str):
    """Returns all unique chats for a user, with last message and worker info."""
    res = await supabase_request(
        "GET", f"messages?chat_id=like.user_{user_id}_worker_*&order=created_at.desc&limit=500"
    )
    messages = res.json()
    seen = {}
    for m in messages:
        cid = m["chat_id"]
        if cid not in seen:
            seen[cid] = m
            
    chats = list(seen.values())

    # Enrichment: Worker Names
    worker_ids = list(set([cid.split("_worker_")[1] for cid in seen.keys() if "_worker_" in cid]))
    if worker_ids:
        # Batch fetch worker names
        ids_str = ",".join([f"{wid}" for wid in worker_ids])
        w_res = await supabase_request("GET", f"workers?id=in.({ids_str})&select=id,name")
        workers_map = {w["id"]: w["name"] for w in w_res.json()}
        for chat in chats:
            wid = chat["chat_id"].split("_worker_")[1]
            chat["worker_name"] = workers_map.get(wid, "Worker")
            
    return JSONResponse({"status": "success", "chats": chats})


@app.post("/api/jobs/request")
async def create_job_request(request: Request):
    try:
        body = await request.json()
        user_id    = body.get("user_id", "")
        user_phone = body.get("user_phone", "")
        worker_id  = body.get("worker_id", "")
        service    = body.get("service", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    if not all([user_id, worker_id]):
        raise HTTPException(status_code=422, detail="user_id and worker_id are required.")

    # Check if a pending/accepted request already exists between this pair
    check = await supabase_request(
        "GET", f"job_requests?user_id=eq.{user_id}&worker_id=eq.{worker_id}&status=in.(pending,accepted,in_progress)&select=id"
    )
    if check.json():
        return JSONResponse({"status": "exists", "message": "Request already active."})

    # ── Subscription Guard ──
    worker_check = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=subscription_status")
    w_data = worker_check.json()
    if w_data and w_data[0].get("subscription_status") == "EXPIRED":
        return JSONResponse({"status": "error", "message": "Worker is currently unavailable (Subscription Expiry)."}, status_code=403)

    res = await supabase_request("POST", "job_requests", data={
        "user_id": user_id,
        "user_phone": user_phone,
        "worker_id": worker_id,
        "service": service,
        "status": "pending",
    })
    job_data = res.json()[0] if isinstance(res.json(), list) else res.json()

    # Real-time Notification to Worker & Admin
    await broadcast_event(f"worker_{worker_id}", "new_job", job_data)
    await notify_admin("job_created", job_data)

    return JSONResponse({"status": "success", "job": job_data})


@app.get("/api/jobs/worker/{worker_id}")
async def get_worker_jobs(worker_id: str):
    res = await supabase_request(
        "GET", f"job_requests?worker_id=eq.{worker_id}&order=created_at.desc&limit=50"
    )
    jobs = res.json()
    for j in jobs:
        if j.get("user_phone"):
            j["user_phone"] = mask_phone(j["user_phone"])
    return JSONResponse({"status": "success", "jobs": jobs})


@app.get("/api/jobs/user/{user_id}")
async def get_user_jobs(user_id: str):
    """Returns all jobs for a user, enriched with worker names."""
    res = await supabase_request(
        "GET", f"job_requests?user_id=eq.{user_id}&order=created_at.desc&limit=50"
    )
    jobs = res.json()
    
    # Enrichment: Worker Names
    worker_ids = list(set([j["worker_id"] for j in jobs if j.get("worker_id")]))
    if worker_ids:
        ids_str = ",".join([f"{wid}" for wid in worker_ids])
        w_res = await supabase_request("GET", f"workers?id=in.({ids_str})&select=id,name")
        worker_map = {w["id"]: w["name"] for w in w_res.json()}
        for j in jobs:
            j["worker_name"] = worker_map.get(j["worker_id"], "Worker")
            
    return JSONResponse({"status": "success", "jobs": jobs})


@app.patch("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, request: Request):
    # ── Subscription Guard ──
    worker_id = request.session.get("worker_id")
    if worker_id:
        worker_res = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=subscription_status")
        w_data = worker_res.json()
        if w_data and w_data[0].get("subscription_status") == "EXPIRED":
            return JSONResponse({"status": "error", "message": "Subscription required to manage jobs."}, status_code=403)

    try:
        body = await request.json()
        new_status = body.get("status", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    allowed = {"pending", "accepted", "rejected", "in_progress", "completed"}
    if new_status not in allowed:
        raise HTTPException(status_code=422, detail=f"Status must be one of {allowed}")

    from datetime import datetime, timezone
    res = await supabase_request("PATCH", f"job_requests?id=eq.{job_id}", data={
        "status": new_status,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    job_data = res.json()[0] if isinstance(res.json(), list) else res.json()

    # Real-time Synchronization to all parties
    job_info_res = await supabase_request("GET", f"job_requests?id=eq.{job_id}&select=user_id,worker_id")
    if job_info_res.is_success and job_info_res.json():
        info = job_info_res.json()[0]
        uid = info.get("user_id")
        wid = info.get("worker_id")
        
        # Notify User
        await broadcast_event(f"user_{uid}", "job_updated", {"job_id": job_id, "status": new_status})
        # Notify Worker (to sync dashboard state instantly)
        await broadcast_event(f"worker_{wid}", "job_state_changed", {"job_id": job_id, "status": new_status})
        
        # Notify admin of key job events (completed)
        if new_status == 'completed':
            await notify_admin('job_completed', {"id": job_id, "status": new_status})

    return JSONResponse({"status": "success", "job": job_data})


@app.post("/api/feedbacks")
async def submit_feedback(request: Request):
    try:
        body = await request.json()
        job_id    = body.get("job_id")
        user_id   = body.get("user_id")
        worker_id = body.get("worker_id")
        rating    = int(body.get("rating"))
        comment   = body.get("comment", "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid feedback data.")

    if not all([job_id, user_id, worker_id, rating]):
        raise HTTPException(status_code=422, detail="Missing required feedback fields.")

    if rating < 1 or rating > 5:
        raise HTTPException(status_code=422, detail="Rating must be between 1 and 5.")

    # Check if feedback already exists for this job
    check = await supabase_request("GET", f"feedbacks?job_id=eq.{job_id}&select=id")
    if check.json():
         return JSONResponse({"status": "exists", "message": "Feedback already submitted."})

    res = await supabase_request("POST", "feedbacks", data={
        "job_id": job_id,
        "user_id": user_id,
        "worker_id": worker_id,
        "rating": rating,
        "comment": comment
    })
    
    # Update worker's rating_avg and review_count (Simplistic version)
    # In a real app, this would be a database function or trigger
    w_res = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=rating_avg,review_count")
    workers = w_res.json()
    if workers:
        w = workers[0]
        new_count = (w['review_count'] or 0) + 1
        curr_avg = w['rating_avg'] or 4.5
        new_avg = ((curr_avg * (new_count - 1)) + rating) / new_count
        await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={
            "rating_avg": round(new_avg, 1),
            "review_count": new_count
        })

    return JSONResponse({"status": "success", "data": res.json()})



# ── SOCKET.IO HANDLERS ──────────────────────────────────────────────────────

@sio.on("connect")
async def handle_connect(sid, environ):
    pass

@sio.on("register")
async def handle_register(sid, data):
    # data: { "id": "user_123" or "worker_456" }
    uid = data.get("id")
    if uid:
        await sio.enter_room(sid, str(uid))
        print(f"Socket registered: {uid} (sid: {sid})")

@sio.on("join")
async def handle_join(sid, data):
    # data: { "chat_id": "..." }
    chat_id = data.get("chat_id")
    if chat_id:
        await sio.enter_room(sid, chat_id)
        print(f"Socket joined room: {chat_id}")

@sio.on("leave")
async def handle_leave(sid, data):
    chat_id = data.get("chat_id")
    if chat_id:
        await sio.leave_room(sid, chat_id)

@sio.on("typing")
async def handle_typing(sid, data):
    # data: { "chat_id": "...", "sender_id": "...", "is_typing": bool }
    chat_id = data.get("chat_id")
    if chat_id:
        await sio.emit("user_typing", data, room=chat_id, skip_sid=sid)

# ── END SOCKET.IO HANDLERS ──────────────────────────────────────────────────

# --- API Endpoints ---
# (Removed duplicate /api/workers route here)

@app.post("/api/register")
async def register_worker(
    name: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    service: str = Form(...),
    experience: int = Form(...),
    area: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    profile_photo: str = Form(None),
    ambassador_id: str = Form(None)
):
    # Check if phone number already exists
    check_response = await supabase_request("GET", f"workers?phone=eq.{phone}")
    existing_workers = check_response.json()
    
    if existing_workers:
        return JSONResponse({"status": "error", "message": "Phone number is already registered."}, status_code=409)
    
    # Hash the password
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    # All new workers start as Offline and Pending approval
    worker_data = {
        "name": name,
        "phone": phone,
        "password_hash": hashed_password,
        "service": service,
        "experience": experience,
        "area": area,
        "latitude": latitude,
        "longitude": longitude,
        "availability": "Offline",
        "is_online": False,
        "account_status": "Pending",
        "status": "offline",
        "profile_photo": profile_photo or f"https://ui-avatars.com/api/?name={name.replace(' ', '+')}&background=random&color=fff",
        "rating_avg": 4.5,
        "review_count": 0,
        "is_verified": False,
        "completed_jobs": 0,
        "verification_status": "PENDING"
    }
    
    if ambassador_id:
        # Try finding by UUID first, then by referral_code
        amb_id_resolved = None
        
        if len(ambassador_id) == 36 and "-" in ambassador_id:
            # Looks like a UUID
            amb_res = await supabase_request("GET", f"ambassadors?id=eq.{ambassador_id}")
            ambs = amb_res.json()
            if ambs:
                amb_id_resolved = ambs[0].get("id")
                ambassador_name = ambs[0].get("name")
                ambassador_phone = ambs[0].get("phone")
        
        if not amb_id_resolved:
            # Try finding by referral_code (case-insensitive)
            amb_res = await supabase_request("GET", f"ambassadors?referral_code=ilike.{ambassador_id}")
            ambs = amb_res.json()
            if ambs:
                amb_id_resolved = ambs[0].get("id")
                ambassador_name = ambs[0].get("name")
                ambassador_phone = ambs[0].get("phone")

        if amb_id_resolved:
            worker_data["ambassador_id"] = amb_id_resolved
            print(f"[REG] Linked worker {name} to ambassador {ambassador_name} ({amb_id_resolved})")
        else:
            print(f"[REG] Warning: Could not resolve ambassador_id '{ambassador_id}'")

    result = await supabase_request("POST", "workers", data=worker_data)
    if not result.is_success:
        print(f"Supabase Error: {result.status_code} - {result.text}")
        return JSONResponse({"status": "error", "message": "Database error occurred. Please ensure DB migration is run."}, status_code=500)
        
    inserted_data = result.json()
    worker_id = inserted_data[0].get("id") if isinstance(inserted_data, list) and len(inserted_data) > 0 else None

    await notify_admin('worker_created', inserted_data[0] if isinstance(inserted_data, list) else inserted_data)

    return JSONResponse({
        "status": "success", 
        "worker_id": worker_id,
        "worker": {
            "name": name,
            "service": service,
            "experience": experience,
            "phone": phone,
            "ambassador_name": ambassador_name,
            "ambassador_phone": ambassador_phone
        }
    })

import asyncio

@app.post("/api/verify/submit")
async def handle_verification_submit(request: Request):
    try:
        body = await request.json()
        worker_id = body.get("worker_id")
        id_front = body.get("id_front")
        id_back = body.get("id_back")
        selfie = body.get("selfie")
        
        if not all([worker_id, id_front, id_back, selfie]):
            return JSONResponse({"status": "error", "message": "Missing images or worker ID."}, status_code=400)
            
        print(f"[{worker_id}] Starting verification upload to Cloudinary...")
        
        # Parallel upload using asyncio.to_thread since cloudinary.uploader.upload is synchronous
        loop = asyncio.get_event_loop()
        upload_tasks = [
            loop.run_in_executor(None, lambda: cloudinary.uploader.upload(id_front, folder=f"nearva/verification/{worker_id}")),
            loop.run_in_executor(None, lambda: cloudinary.uploader.upload(id_back, folder=f"nearva/verification/{worker_id}")),
            loop.run_in_executor(None, lambda: cloudinary.uploader.upload(selfie, folder=f"nearva/verification/{worker_id}"))
        ]
        results = await asyncio.gather(*upload_tasks)
        
        id_front_url = results[0].get("secure_url")
        id_back_url = results[1].get("secure_url")
        selfie_url = results[2].get("secure_url")
        
        # Insert into verifications table
        verification_data = {
            "worker_id": worker_id,
            "id_front_url": id_front_url,
            "id_back_url": id_back_url,
            "selfie_url": selfie_url,
            "status": "UNDER_REVIEW"
        }
        v_res = await supabase_request("POST", "verifications", data=verification_data)
        if not v_res.is_success:
            print(f"Supabase error inserting verification: {v_res.text}")
            return JSONResponse({"status": "error", "message": "Failed to save verification record."}, status_code=500)
            
        # Update worker verification_status
        w_res = await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={"verification_status": "UNDER_REVIEW"})
        if not w_res.is_success:
            print(f"Supabase error updating worker status: {w_res.text}")
        
        await notify_admin('worker_updated', {"id": worker_id, "verification_status": "UNDER_REVIEW"})
            
        return JSONResponse({"status": "success", "message": "Verification submitted successfully."})
        
    except Exception as e:
        print(f"Error handling verification submit: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# --- Admin API ---

@app.post("/api/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    # Use environment variable or fallback to user specified password
    if password == os.environ.get("ADMIN_PASSWORD", "AB125467#aaa1"):
        request.session["admin_logged_in"] = True
        return JSONResponse({"status": "success"})
    return JSONResponse({"status": "error"}, status_code=401)

@app.get("/api/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin")

@app.get("/api/admin/workers")
async def get_all_workers(request: Request):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    response = await supabase_request("GET", "workers?select=*,ambassadors(name)&order=created_at.desc")
    return response.json()

@app.post("/api/admin/approve/{worker_id}")
async def approve_worker(worker_id: str):
    await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={"account_status": "Approved"})
    await notify_admin('worker_updated', {"id": worker_id, "account_status": "Approved"})
    return {"status": "success"}

@app.post("/api/admin/reject/{worker_id}")
async def reject_worker(worker_id: str):
    await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={"account_status": "Rejected"})
    await notify_admin('worker_updated', {"id": worker_id, "account_status": "Rejected"})
    return {"status": "success"}

@app.delete("/api/admin/delete/{worker_id}")
async def delete_worker(worker_id: str):
    await supabase_request("DELETE", f"workers?id=eq.{worker_id}")
    await notify_admin('worker_deleted', {"id": worker_id})
    return {"status": "success"}

@app.post("/api/admin/toggle_availability/{worker_id}")
async def toggle_availability(worker_id: str):
    # First get current status
    response = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=availability")
    workers = response.json()
    if not workers:
        return JSONResponse({"status": "error", "message": "Worker not found"}, status_code=404)
    
    current_status = workers[0]['availability']
    new_status = "Busy" if current_status == "Available" else "Available"
    is_online = (new_status != "Offline")
    
    # Update availability and is_online
    await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={"availability": new_status, "is_online": is_online})
    return {"status": "success", "new_status": new_status}

# --- Ambassador API ---

@app.get("/api/ambassadors")
async def get_ambassadors():
    # Fetch all active ambassadors
    amb_resp = await supabase_request("GET", "ambassadors?status=eq.active&select=id,name,area,phone&order=name.asc")
    ambs = amb_resp.json() if amb_resp.is_success else []
    
    # Efficiently fetch worker counts
    if ambs:
        try:
            counts_resp = await supabase_request("GET", "workers?select=ambassador_id,account_status&ambassador_id=not.is.null")
            if counts_resp.is_success:
                workers_raw = counts_resp.json()
                from collections import defaultdict
                stats_map = defaultdict(lambda: {"total": 0, "approved": 0, "pending": 0})
                for w in workers_raw:
                    aid = w.get("ambassador_id")
                    if aid:
                        stats_map[aid]["total"] += 1
                        if w.get("account_status") == "Approved":
                            stats_map[aid]["approved"] += 1
                        else:
                            stats_map[aid]["pending"] += 1
                for amb in ambs:
                    amb["stats"] = stats_map.get(amb["id"], {"total": 0, "approved": 0, "pending": 0})
        except Exception as e:
            for amb in ambs:
                amb["stats"] = {"total": 0, "approved": 0, "pending": 0}
                
    return JSONResponse(ambs)

@app.get("/api/public/ambassador/{ambassador_id}/stats")
async def public_ambassador_stats(ambassador_id: str):
    """Fetch live stats for a specific ambassador for UI expansions."""
    if not ambassador_id: return JSONResponse({"error": "Missing ID"}, status_code=400)
    
    try:
        resp = await supabase_request("GET", f"workers?select=account_status&ambassador_id=eq.{ambassador_id}")
        workers = resp.json() if resp.is_success else []
        
        total = len(workers)
        approved = len([w for w in workers if w.get("account_status") == "Approved"])
        pending = total - approved
        
        return JSONResponse({"total_workers": total, "approved_workers": approved, "pending_workers": pending})
    except:
        return JSONResponse({"total_workers": 0, "approved_workers": 0, "pending_workers": 0})

@app.post("/api/admin/ambassadors")
async def create_ambassador(request: Request, name: str = Form(...), phone: str = Form(...), area: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error"}, status_code=401)
    # Ensure phone number formatting is valid (simple strip)
    phone = "".join(filter(str.isdigit, phone))[-10:]
    data = {"name": name, "phone": phone, "area": area, "status": "active"}
    res = await supabase_request("POST", "ambassadors", data=data)
    if res.status_code >= 400: return JSONResponse({"status": "error", "message": res.text}, status_code=400)
    return {"status": "success"}



@app.post("/api/admin/ambassadors/{id}/toggle")
async def toggle_ambassador(request: Request, id: str):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error"}, status_code=401)
    amb_res = await supabase_request("GET", f"ambassadors?id=eq.{id}")
    ambs = amb_res.json()
    if not ambs: return {"status": "error"}
    new_status = "inactive" if ambs[0]["status"] == "active" else "active"
    await supabase_request("PATCH", f"ambassadors?id=eq.{id}", data={"status": new_status})
    return {"status": "success", "new_status": new_status}

# --- Worker Real-Time API ---

@app.get("/login")
async def login_redirect():
    """Redirect legacy login link to the correct worker login page."""
    return RedirectResponse(url="/worker/login")

@app.get("/worker/login", response_class=HTMLResponse)
async def worker_login_page(request: Request):
    if request.session.get("worker_id"):
        return RedirectResponse(url="/worker/dashboard")
    return templates.TemplateResponse(request=request, name="worker_login.html")

@app.get("/worker/dashboard", response_class=HTMLResponse)
async def worker_dashboard_page(request: Request):
    worker_id = request.session.get("worker_id")
    if not worker_id:
        return RedirectResponse(url="/worker/login")
    return templates.TemplateResponse(request=request, name="worker_dashboard.html", context={"worker_id": worker_id})

@app.get("/worker/profile", response_class=HTMLResponse)
async def worker_profile_page(request: Request):
    worker_id = request.session.get("worker_id")
    if not worker_id:
        return RedirectResponse(url="/worker/login")
    return templates.TemplateResponse(request=request, name="worker_profile.html", context={"worker_id": worker_id})

@app.get("/api/cloudinary/signature")
async def get_cloudinary_signature(request: Request):
    session_worker_id = request.session.get("worker_id")
    if not session_worker_id:
        return JSONResponse({"status": "error", "message": "Unauthorized access."}, status_code=403)
        
    timestamp = int(time.time())
    params_to_sign = {
        "timestamp": timestamp,
        "folder": "nearva_profiles"
    }

    signature = cloudinary.utils.api_sign_request(params_to_sign, os.environ.get('CLOUDINARY_API_SECRET'))

    return {"signature": signature, "timestamp": timestamp, "cloud_name": os.environ.get('CLOUDINARY_CLOUD_NAME'), "api_key": os.environ.get('CLOUDINARY_API_KEY'), "folder": "nearva_profiles"}

@app.post("/api/worker/profile")
async def update_worker_profile(
    request: Request,
    id: str = Form(...),
    name: str = Form(...),
    service: str = Form(...),
    experience: int = Form(...),
    profile_photo: str = Form(None)
):
    session_worker_id = request.session.get("worker_id")
    if str(session_worker_id) != str(id):
        return JSONResponse({"status": "error", "message": "Unauthorized access."}, status_code=403)
        
    data = {
        "name": name,
        "service": service,
        "experience": experience
    }
    if profile_photo:
        data["profile_photo"] = profile_photo
        
    await supabase_request("PATCH", f"workers?id=eq.{id}", data=data)
    return {"status": "success"}

@app.get("/api/worker/profile/{worker_id}")
async def get_worker_profile(worker_id: str, request: Request):
    session_worker_id = request.session.get("worker_id")
    if str(session_worker_id) != str(worker_id):
        return JSONResponse({"status": "error", "message": "Unauthorized access."}, status_code=403)
        
    response = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=id,name,phone,service,experience,profile_photo")
    workers = response.json()
    if workers:
        return {"status": "success", "worker": workers[0]}
    return JSONResponse({"status": "error", "message": "Worker not found."}, status_code=404)

@app.post("/api/worker/login")
async def worker_login(request: Request, phone: str = Form(...), password: str = Form(...)):
    # Fetch worker by phone
    response = await supabase_request("GET", f"workers?phone=eq.{phone}&select=id,name,account_status,password_hash")
    workers = response.json()
    
    if not workers:
        return JSONResponse({"status": "error", "message": "Phone number not found."}, status_code=404)
    
    worker = workers[0]
    
    # Verify password hash (and handle legacy workers without password)
    stored_hash = worker.get('password_hash')
    if not stored_hash or not bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
        return JSONResponse({"status": "error", "message": "Invalid password or account requires password setup."}, status_code=401)
    
    # Check account_status
    if worker.get('account_status') == 'Pending':
        return JSONResponse({"status": "error", "message": "Account pending approval."}, status_code=403)
    elif worker.get('account_status') == 'Rejected':
        return JSONResponse({"status": "error", "message": "Account rejected by admin."}, status_code=403)
    
    # Secure Session Login
    request.session["worker_id"] = worker["id"]
    request.session["worker_role"] = "worker"
    
    return {"status": "success", "worker": {"id": worker["id"], "name": worker["name"], "status": worker.get("account_status")}}

@app.get("/api/worker/logout")
async def worker_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/worker/login")

@app.post("/api/worker/update")
async def worker_update(
    request: Request,
    id: str = Form(...),
    latitude: float = Form(None),
    longitude: float = Form(None),
    availability: str = Form(...) # 'Available' or 'Offline'
):
    # Verify session matches the worker being updated
    session_worker_id = request.session.get("worker_id")
    if not session_worker_id or session_worker_id != id:
        return JSONResponse({"status": "error", "message": "Unauthorized access."}, status_code=403)
    # SECURITY: Validate worker is approved before allowing updates
    check_response = await supabase_request("GET", f"workers?id=eq.{id}&select=account_status")
    workers = check_response.json()
    
    if not workers:
        return JSONResponse({"status": "error", "message": "Worker not found."}, status_code=404)
    
    worker_status = workers[0].get('account_status')
    if worker_status != 'Approved':
        return JSONResponse({
            "status": "error", 
            "message": "Only approved workers can update their status."
        }, status_code=403)
    
    # Proceed with update if approved
    is_online = (availability != "Offline")
    data = {
        "availability": availability,
        "is_online": is_online,
        "last_active": "now()" # PostgREST shorthand
    }
    
    from datetime import datetime, timezone
    data["last_active"] = datetime.now(timezone.utc).isoformat()
    
    if latitude is not None and longitude is not None:
        data["latitude"] = latitude
        data["longitude"] = longitude
    
    await supabase_request("PATCH", f"workers?id=eq.{id}", data=data)
    return {"status": "success", "is_online": is_online, "availability": availability}

# ── USER OTP LOGIN (MSG91) ─────────────────────────────────────────────────────

MSG91_SEND_URL = "https://api.msg91.com/api/v5/otp"
MSG91_VERIFY_URL = "https://api.msg91.com/api/v5/otp/verify"

# In-memory rate limiter for OTP (phone → list of timestamps)
USER_OTP_LIMITER: dict = {}
# In-memory OTP store for dev mode (phone → {otp, expires_at})
DEV_OTP_STORE = {}

def _check_otp_rate_limit(phone: str):
    """Allow max 3 OTP send attempts per 5 minutes per phone number."""
    now = datetime.now(timezone.utc).timestamp()
    history = USER_OTP_LIMITER.get(phone, [])
    history = [t for t in history if now - t < 300]  # 5-min rolling window
    if len(history) >= 3:
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Please wait 5 minutes before trying again."
        )
    history.append(now)
    USER_OTP_LIMITER[phone] = history


@app.get("/user/login", response_class=HTMLResponse)
async def user_login_page(request: Request, next: str = "/"):
    """Serve the user OTP login page. Redirect to 'next' if already logged in."""
    if request.session.get("user_id"):
        return RedirectResponse(url=next)
    return templates.TemplateResponse(request=request, name="user_login.html", context={
        "widget_id": os.environ.get("MSG91_WIDGET_ID", ""),
        "widget_token": os.environ.get("MSG91_WIDGET_TOKEN", ""),
        "next_url": next
    })


@app.get("/api/user/logout")
async def user_logout(request: Request):
    """Log out the current user and redirect to home."""
    request.session.pop("user_id", None)
    request.session.pop("user_phone", None)
    return RedirectResponse(url="/")


@app.post("/api/user/verify-widget-token")
async def user_verify_widget_token(request: Request):
    """Verify MSG91 Widget access_token and establish user session."""
    try:
        body = await request.json()
        access_token = str(body.get("access_token", "")).strip()
        phone_raw = str(body.get("phone", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    if not access_token:
        raise HTTPException(status_code=422, detail="Missing access token.")

    phone = "".join(filter(str.isdigit, phone_raw))
    msg91_auth_key = os.environ.get("MSG91_AUTH_KEY", "")

    # Verify the widget access token with MSG91
    try:
        verify_res = await http_client.post(
            "https://control.msg91.com/api/v5/widget/verifyAccessToken",
            headers={"Content-Type": "application/json"},
            json={"authkey": msg91_auth_key, "access_token": access_token},
            timeout=10.0
        )
        print(f"MSG91 Widget Verify [{verify_res.status_code}]: {verify_res.text}")
        result = verify_res.json()
    except httpx.RequestError as e:
        print(f"MSG91 Widget Verify Error: {str(e)}")
        raise HTTPException(status_code=503, detail="Verification service unavailable.")

    # Check verification result
    if result.get("type") != "success":
        err_msg = result.get("message", "Token verification failed.")
        raise HTTPException(status_code=401, detail=f"Verification failed: {err_msg}")

    # Extract phone from MSG91 response if not provided
    verified_mobile = result.get("mobile", result.get("phone", ""))
    if verified_mobile:
        phone = "".join(filter(str.isdigit, str(verified_mobile)))[-10:]

    if not phone or len(phone) != 10:
        raise HTTPException(status_code=422, detail="Unable to determine phone number from verification.")

    # Upsert user in Supabase
    try:
        check_res = await supabase_request("GET", f"users?phone=eq.{phone}&select=id,phone")
        existing_users = check_res.json()
        if existing_users:
            user = existing_users[0]
            now_iso = datetime.now(timezone.utc).isoformat()
            await supabase_request("PATCH", f"users?id=eq.{user['id']}", data={"last_login": now_iso})
            print(f"[SUCCESS] Existing user logged in via Widget: {phone}")
        else:
            create_res = await supabase_request("POST", "users", data={"phone": phone})
            user = create_res.json()[0]
            print(f"[SUCCESS] New user registered via Widget: {phone}")
            await notify_admin('user_created', user)

        request.session["user_id"] = user["id"]
        request.session["user_phone"] = phone
        return JSONResponse({"status": "success", "message": "Login successful.", "redirect": "/"})

    except HTTPException as e:
        raise HTTPException(status_code=500, detail="Account setup failed. Please try again.")


@app.post("/api/user/send-otp")
async def user_send_otp(request: Request):
    try:
        body = await request.json()
        phone_raw = body.get("phone", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # Validate 10-digit phone
    phone = "".join(filter(str.isdigit, phone_raw))[-10:]
    if len(phone) != 10:
        raise HTTPException(status_code=422, detail="Invalid 10-digit phone number.")

    # Rate limit check
    _check_otp_rate_limit(phone)

    dev_mode = os.environ.get("OTP_DEV_MODE", "false").lower() == "true"
    
    if dev_mode:
        import random
        otp = str(random.randint(1000, 9999))
        expiry = datetime.now(timezone.utc).timestamp() + 300
        DEV_OTP_STORE[phone] = {"otp": otp, "expires_at": expiry}
        
        print("\n" + "="*50)
        print(f"[OTP] DEV MODE OTP for +91{phone}: {otp}")
        print("="*50 + "\n")
        
        return JSONResponse({"status": "success", "message": "OTP generated (Dev Mode)."})

    # ── PRODUCTION: Send via MSG91 v5 OTP API ─────────────────────────────
    msg91_auth_key = os.environ.get("MSG91_AUTH_KEY", "")
    msg91_template_id = os.environ.get("MSG91_TEMPLATE_ID", "")
    
    if not msg91_auth_key or msg91_auth_key == "YOUR_MSG91_AUTH_KEY_HERE":
        raise HTTPException(status_code=503, detail="OTP service not configured.")

    try:
        params = {
            "template_id": msg91_template_id,
            "mobile": f"91{phone}",
            "authkey": msg91_auth_key,
        }
        res = await http_client.get("https://api.msg91.com/api/v5/otp", params=params, timeout=10.0)
        result = res.json()
        
        if result.get("type") == "success":
            print(f"[SUCCESS] OTP sent successfully via MSG91 to {phone}")
            return JSONResponse({"status": "success", "message": "OTP sent successfully."})
        else:
            print(f"[ERROR] MSG91 Send OTP Error: {result.get('message')}")
            raise HTTPException(status_code=502, detail=f"Failed to send OTP: {result.get('message')}")
            
    except Exception as e:
        print(f"[ERROR] Connection error sending OTP: {str(e)}")
        raise HTTPException(status_code=503, detail="OTP service currently unavailable.")


@app.post("/api/user/verify-otp")
async def user_verify_otp(request: Request):
    try:
        body = await request.json()
        phone_raw = body.get("phone", "")
        otp = str(body.get("otp", "")).strip()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")

    # Validate inputs
    phone = "".join(filter(str.isdigit, phone_raw))[-10:]
    # Support redirection after login (Capture early for use in all branches)
    next_url = body.get("next", "/")

    # ── HARDCODED TEST ACCOUNTS (Always work, bypasses MSG91) ─────────────
    TEST_ACCOUNTS = {
        "9597482995": "1234",
        "7418562267": "5678",
        "6381279316": "9012"
    }
    
    if phone in TEST_ACCOUNTS and TEST_ACCOUNTS[phone] == otp:
        print(f"[BYPASS] TEST ACCOUNT BYPASS: {phone} logged in with {otp}")
        check_res = await supabase_request("GET", f"users?phone=eq.{phone}&select=id,phone")
        existing_users = check_res.json()
        if existing_users:
            user = existing_users[0]
            await supabase_request("PATCH", f"users?id=eq.{user['id']}", data={"last_login": datetime.now(timezone.utc).isoformat()})
        else:
            create_res = await supabase_request("POST", "users", data={"phone": phone})
            user = create_res.json()[0]
            await notify_admin('user_created', user)

        request.session["user_id"] = user["id"]
        request.session["user_phone"] = phone
        return JSONResponse({"status": "success", "message": "Login successful (Test Bypass).", "redirect": "/user/dashboard"})

    dev_mode = os.environ.get("OTP_DEV_MODE", "false").lower() == "true"
    
    # ── DEV MODE: Verify from in-memory store ─────────────────────────────
    if dev_mode:
        now = datetime.now(timezone.utc).timestamp()
        stored = DEV_OTP_STORE.get(phone)
        if not stored or stored["otp"] != otp:
             raise HTTPException(status_code=401, detail="Invalid OTP. Please try again.")
        if now > stored["expires_at"]:
            DEV_OTP_STORE.pop(phone, None)
            raise HTTPException(status_code=401, detail="OTP expired. Request a new one.")
        
        DEV_OTP_STORE.pop(phone, None)
        print(f"[SUCCESS] DEV MODE: OTP verified for {phone}")
        
        check_res = await supabase_request("GET", f"users?phone=eq.{phone}&select=id,phone")
        existing_users = check_res.json()
        if existing_users:
            user = existing_users[0]
            await supabase_request("PATCH", f"users?id=eq.{user['id']}", data={"last_login": datetime.now(timezone.utc).isoformat()})
        else:
            create_res = await supabase_request("POST", "users", data={"phone": phone})
            user = create_res.json()[0]
            await notify_admin('user_created', user)

        request.session["user_id"] = user["id"]
        request.session["user_phone"] = phone
        return JSONResponse({"status": "success", "message": "Login successful (Dev Mode).", "redirect": next_url})

    # ── PRODUCTION: Verify via MSG91 v5 API ───────────────────────────────
    msg91_auth_key = os.environ.get("MSG91_AUTH_KEY", "")
    try:
        params = {
            "authkey": msg91_auth_key,
            "mobile": f"91{phone}",
            "otp": otp
        }
        res = await http_client.get("https://api.msg91.com/api/v5/otp/verify", params=params, timeout=10.0)
        result = res.json()
        
        if result.get("type") != "success":
            print(f"[ERROR] OTP Verify Error: {result.get('message')}")
            raise HTTPException(status_code=401, detail=result.get("message", "Invalid or expired OTP."))

        check_res = await supabase_request("GET", f"users?phone=eq.{phone}&select=id,phone")
        existing_users = check_res.json()

        if existing_users:
            user = existing_users[0]
            await supabase_request("PATCH", f"users?id=eq.{user['id']}", data={"last_login": datetime.now(timezone.utc).isoformat()})
            print(f"[SUCCESS] Existing user logged in via real OTP: {phone}")
        else:
            create_res = await supabase_request("POST", "users", data={"phone": phone})
            user = create_res.json()[0]
            print(f"[SUCCESS] New user registered via real OTP: {phone}")
            await notify_admin('user_created', user)

        request.session["user_id"] = user["id"]
        request.session["user_phone"] = phone
        return JSONResponse({"status": "success", "message": "Login successful.", "redirect": next_url})

    except Exception as e:
        if isinstance(e, HTTPException): raise e
        print(f"[ERROR] Unexpected verification error: {str(e)}")
        raise HTTPException(status_code=500, detail="Authentication failed. Please try again.")

    finally:
        pass # End of function logic


# Basic in-memory rate limiter (worker status updates)
RE_LIMITER = {}


@app.put("/api/worker/status")
async def update_worker_status(request: Request):
    session_worker_id = request.session.get("worker_id")
    if not session_worker_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        body = await request.json()
        new_status = body.get("status") # 'online' or 'offline'
        lat = body.get("latitude")
        lng = body.get("longitude")
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if new_status not in ["online", "offline"]:
        raise HTTPException(status_code=422, detail="Status must be 'online' or 'offline'")

    # STEP 1 FIX: If going online, GPS is MANDATORY
    if new_status == "online":
        if lat is None or lng is None:
            raise HTTPException(status_code=400, detail="GPS location required to go online")

    # Basic Rate Limiting
    now_ts = datetime.now(timezone.utc).timestamp()
    history = RE_LIMITER.get(session_worker_id, [])
    history = [t for t in history if now_ts - t < 60]
    if len(history) >= 10: # Allow more updates for heartbeat
        raise HTTPException(status_code=429, detail="Too many updates. Please wait.")
    history.append(now_ts)
    RE_LIMITER[session_worker_id] = history

    # Update database
    is_online = (new_status == "online")
    now_iso = datetime.now(timezone.utc).isoformat()
    
    data = {
        "status": new_status,
        "is_online": is_online,
        "availability": "Available" if is_online else "Offline",
        "last_status_update": now_iso,
        "last_active": now_iso
    }
    
    if lat is not None: data["latitude"] = lat
    if lng is not None: data["longitude"] = lng
    
    response = await supabase_request("PATCH", f"workers?id=eq.{session_worker_id}", data=data)
    
    if response.status_code >= 400:
        error_detail = response.text
        print(f"ERROR: Failed to update status for {session_worker_id}: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Database update failed: {error_detail}")

    print(f"LOG: Worker {session_worker_id} is now {'ONLINE' if is_online else 'OFFLINE'} at {now_iso}")
    
    await notify_admin('worker_updated', {"id": session_worker_id, "is_online": is_online, "status": new_status})
    
    return {
        "status": "success",
        "is_online": is_online,
        "last_update": now_iso
    }

@app.get("/api/worker/status/{worker_id}")
async def get_worker_status(worker_id: str, request: Request):
    session_worker_id = request.session.get("worker_id")
    if not session_worker_id or session_worker_id != worker_id:
        return JSONResponse({"status": "error", "message": "Unauthorized access."}, status_code=403)
    
    # ── Fetch All Critical Status Fields ──
    response = await supabase_request("GET", 
        f"workers?id=eq.{worker_id}&select=verification_status,account_status,name,status,last_status_update,is_online,last_active,created_at,subscription_status,subscription_expiry")
    workers = response.json()
    
    if not workers:
        return JSONResponse({"status": "error", "message": "Worker not found."}, status_code=404)
        
    worker = workers[0]
    
    # ── Calculate Computed Subscription Status ──
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    
    # Parse Created At (Handling Z or offset)
    created_at_str = worker.get("created_at", now.isoformat())
    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
    
    current_status = worker.get("subscription_status", "FREE")
    expiry_str = worker.get("subscription_expiry")
    expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00')) if expiry_str else None
    
    computed_status = current_status
    
    if current_status == "FREE":
        # Check 90 day limit
        if now > (created_at + timedelta(days=90)):
            computed_status = "EXPIRED"
    elif current_status == "ACTIVE" and expiry:
        # Check subscription expiry
        if now > expiry:
            computed_status = "EXPIRED"
            
    # Lazy update DB if status changed to EXPIRED
    if computed_status == "EXPIRED" and current_status != "EXPIRED":
        await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={"subscription_status": "EXPIRED"})
        worker["subscription_status"] = "EXPIRED"
    else:
        worker["subscription_status"] = computed_status

    return {"status": "success", "worker": worker}

@app.post("/api/worker/subscribe")
async def worker_subscribe(request: Request):
    worker_id = request.session.get("worker_id")
    if not worker_id:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
        
    from datetime import datetime, timezone, timedelta
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    
    data = {
        "subscription_status": "ACTIVE",
        "subscription_expiry": expiry
    }
    
    resp = await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data=data)
    if not resp.is_success:
        return JSONResponse({"status": "error", "message": "Payment simulation failed"}, status_code=500)
        
    return {"status": "success", "message": "Subscription activated for 30 days", "expiry": expiry}


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMUNITY AMBASSADOR SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

# ── Password helpers (SHA-256 — clean hex, no encoding issues) ──────────────
def _amb_hash(password: str) -> str:
    """Return SHA-256 hex digest of password (simple, reliable, no binary encoding issues)."""
    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()

def _amb_verify(password: str, stored: str) -> bool:
    """Verify ambassador password against stored hash (SHA-256 or bcrypt legacy)."""
    if not stored:
        return False
    # SHA-256 hashes are exactly 64 hex chars
    if len(stored) == 64:
        return secrets.compare_digest(_amb_hash(password), stored)
    # Fallback: bcrypt (for any legacy records)
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False

# ── Special headers that bypass RLS (service_role key already set globally) ──
def _admin_headers():
    """Return Supabase headers with explicit service-role bypass and representation."""
    return {
        "apikey":         SUPABASE_KEY,
        "Authorization":  f"Bearer {SUPABASE_KEY}",
        "Content-Type":   "application/json",
        "Prefer":         "return=representation",
        "X-Client-Info":  "nearva-admin/1.0",
    }

async def _supabase_admin_patch(endpoint: str, data: dict):
    """PATCH using service-role headers that bypass all RLS."""
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    resp = await http_client.request("PATCH", url, headers=_admin_headers(), json=data)
    print(f"[ADMIN PATCH] {endpoint} -> {resp.status_code} | {resp.text[:200]}")
    return resp

async def _supabase_admin_post(endpoint: str, data: dict):
    """POST using service-role headers that bypass all RLS."""
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    resp = await http_client.request("POST", url, headers=_admin_headers(), json=data)
    print(f"[ADMIN POST] {endpoint} -> {resp.status_code} | {resp.text[:200]}")
    return resp


@app.get("/ambassador/login", response_class=HTMLResponse)
async def ambassador_login_page(request: Request):
    if request.session.get("ambassador_id"):
        return RedirectResponse(url="/ambassador/dashboard")
    return templates.TemplateResponse(request=request, name="ambassador_login.html")

@app.post("/ambassador/login")
async def ambassador_login(
    request: Request,
    phone: str = Form(...),
    password: str = Form(...)
):
    phone_clean = phone.strip().replace(" ", "")
    print(f"[AMB LOGIN] phone='{phone_clean}'")

    resp = await supabase_request("GET",
        f"ambassadors?phone=eq.{phone_clean}&select=id,name,phone,area,password_hash,status")
    records = resp.json()
    print(f"[AMB LOGIN] records found: {len(records)}")

    if not records:
        return templates.TemplateResponse(request=request, name="ambassador_login.html",
            context={"error": "No account found for this phone number."})

    amb = records[0]
    stored = amb.get("password_hash") or ""
    print(f"[AMB LOGIN] name={amb.get('name')} status={amb.get('status')} hash_len={len(stored)}")

    if amb.get("status") != "active":
        return templates.TemplateResponse(request=request, name="ambassador_login.html",
            context={"error": "Your account is deactivated. Contact Admin."})

    if not stored:
        return templates.TemplateResponse(request=request, name="ambassador_login.html",
            context={"error": "Password not set. Admin must open 'Add Ambassador' and re-enter your phone + new password."})

    if not _amb_verify(password, stored):
        return templates.TemplateResponse(request=request, name="ambassador_login.html",
            context={"error": "Incorrect password. Please try again."})

    request.session["ambassador_id"]    = amb["id"]
    request.session["ambassador_name"]  = amb["name"]
    request.session["ambassador_area"]  = amb["area"]
    request.session["ambassador_phone"] = amb["phone"]
    return RedirectResponse(url="/ambassador/dashboard", status_code=302)


@app.get("/ambassador/dashboard", response_class=HTMLResponse)
async def ambassador_dashboard(request: Request):
    ambassador_id = request.session.get("ambassador_id")
    if not ambassador_id:
        return RedirectResponse(url="/ambassador/login")
    return templates.TemplateResponse(request=request, name="ambassador_dashboard.html", context={
        "ambassador_name":  request.session.get("ambassador_name", "Ambassador"),
        "ambassador_area":  request.session.get("ambassador_area", "Your Area"),
        "ambassador_phone": request.session.get("ambassador_phone", ""),
        "ambassador_id":    ambassador_id,
    })

@app.get("/api/ambassador/logout")
async def ambassador_logout(request: Request):
    for k in ("ambassador_id", "ambassador_name", "ambassador_area", "ambassador_phone"):
        request.session.pop(k, None)
    return RedirectResponse(url="/ambassador/login")

@app.get("/api/ambassador/workers")
async def ambassador_get_workers(request: Request):
    ambassador_id = request.session.get("ambassador_id")
    if not ambassador_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    resp = await supabase_request("GET",
        f"workers?ambassador_id=eq.{ambassador_id}&select=id,name,phone,service,area,account_status,verification_status,profile_photo,created_at&order=created_at.desc")
    workers = resp.json() if resp.is_success else []
    return JSONResponse({"status": "success", "workers": workers})

@app.get("/api/ambassador/stats")
async def ambassador_get_stats(request: Request):
    ambassador_id = request.session.get("ambassador_id")
    if not ambassador_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    resp = await supabase_request("GET",
        f"workers?ambassador_id=eq.{ambassador_id}&select=account_status")
    workers = resp.json() if resp.is_success else []
    total    = len(workers)
    verified = sum(1 for w in workers if w.get("verification_status") == "VERIFIED")
    review   = sum(1 for w in workers if w.get("verification_status") == "UNDER_REVIEW")
    pending  = sum(1 for w in workers if w.get("verification_status") == "PENDING")
    rejected = sum(1 for w in workers if w.get("verification_status") == "REJECTED")
    
    return JSONResponse({
        "status": "success",
        "total_workers":    total,
        "approved_workers": verified,
        "pending_workers":  pending,
        "review_workers":   review,
        "rejected_workers": rejected
    })

async def _ambassador_set_status(worker_id: str, new_status: str, request: Request):
    """Shared helper for ambassador approve/reject. Returns JSONResponse."""
    ambassador_id = request.session.get("ambassador_id")
    if not ambassador_id:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    # Primary check: worker must be linked to this ambassador via FK
    check = await supabase_request("GET",
        f"workers?id=eq.{worker_id}&ambassador_id=eq.{ambassador_id}&select=id,name")
    worker_rows = check.json() if check.is_success else []

    # Fallback check: worker ID exists at all (for pre-migration workers that lack ambassador_id)
    if not worker_rows:
        fallback = await supabase_request("GET", f"workers?id=eq.{worker_id}&select=id,name,ambassador_id")
        fb_rows = fallback.json() if fallback.is_success else []
        if fb_rows:
            existing_amb = fb_rows[0].get("ambassador_id")
            if existing_amb and existing_amb != ambassador_id:
                print(f"[AMB ACTION] DENIED: worker {worker_id} belongs to different ambassador {existing_amb}")
                return JSONResponse({"status": "error", "message": "This worker is not assigned to you"}, status_code=403)
            # Worker exists but has no ambassador_id (pre-migration) — claim + update
            print(f"[AMB ACTION] Pre-migration worker {worker_id} - linking to ambassador {ambassador_id}")
            await supabase_request("PATCH", f"workers?id=eq.{worker_id}",
                data={"ambassador_id": ambassador_id})
        else:
            return JSONResponse({"status": "error", "message": "Worker not found"}, status_code=404)

    result = await supabase_request("PATCH", f"workers?id=eq.{worker_id}",
        data={"account_status": new_status})

    if not result.is_success:
        print(f"[AMB ACTION] DB PATCH failed for worker {worker_id}: {result.status_code} {result.text[:80]}")
        return JSONResponse({"status": "error", "message": "Database update failed"}, status_code=500)

    print(f"[AMB ACTION] Worker {worker_id} -> {new_status} by ambassador {ambassador_id}")
    return JSONResponse({"status": "success", "account_status": new_status})

@app.post("/api/ambassador/approve/{worker_id}")
async def ambassador_approve_worker(worker_id: str, request: Request):
    return await _ambassador_set_status(worker_id, "Approved", request)

@app.post("/api/ambassador/reject/{worker_id}")
async def ambassador_reject_worker(worker_id: str, request: Request):
    return await _ambassador_set_status(worker_id, "Rejected", request)

@app.get("/api/ambassador/verification/{worker_id}")
async def get_worker_verification(worker_id: str, request: Request):
    if not request.session.get("ambassador_id"):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    res = await supabase_request("GET", f"verifications?worker_id=eq.{worker_id}&order=created_at.desc&limit=1")
    verifs = res.json()
    if not verifs:
        return JSONResponse({"status": "error", "message": "No verification found"}, status_code=404)
    return JSONResponse({"status": "success", "verification": verifs[0]})

@app.post("/api/ambassador/verify_action/{worker_id}")
async def action_worker_verification(worker_id: str, request: Request):
    if not request.session.get("ambassador_id"):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
        
    try:
        body = await request.json()
        action = body.get("action") # 'approve' or 'reject'
        
        if action not in ['approve', 'reject']:
            return JSONResponse({"status": "error", "message": "Invalid action"}, status_code=400)
            
        new_status = "VERIFIED" if action == "approve" else "REJECTED"
        mapped_account = "Approved" if action == "approve" else "Rejected"
        
        # update verification
        v_res = await supabase_request("PATCH", f"verifications?worker_id=eq.{worker_id}", data={"status": new_status})
        
        # update worker
        w_res = await supabase_request("PATCH", f"workers?id=eq.{worker_id}", data={
            "verification_status": new_status,
            "account_status": mapped_account
        })
        
        if not w_res.is_success:
            return JSONResponse({"status": "error", "message": "Failed to update worker"}, status_code=500)
            
        await notify_admin('worker_updated', {"id": worker_id, "verification_status": new_status, "account_status": mapped_account})
            
        return JSONResponse({"status": "success", "new_verification_status": new_status, "new_account_status": mapped_account})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)




# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN – AMBASSADOR MANAGEMENT APIS
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/api/admin/status")
async def admin_status(request: Request):
    """Diagnostic endpoint to verify if the admin session is currently active."""
    is_logged_in = request.session.get("admin_logged_in", False)
    return JSONResponse({"logged_in": is_logged_in})

@app.get("/api/admin/ambassadorsList")
async def admin_list_ambassadors(request: Request):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
        
    try:
        # Step 1: Fetch all ambassadors
        amb_resp = await supabase_request("GET",
            "ambassadors?select=id,name,phone,area,status,referral_code,created_at&order=created_at.desc")
        
        print(f"DEBUG [Admin]: Ambassadors Fetch Status: {amb_resp.status_code}")
        if not amb_resp.is_success:
            print(f"DEBUG [Admin]: Ambassadors Fetch Error: {amb_resp.text}")
            
        ambassadors = amb_resp.json() if amb_resp.is_success else []
        print(f"DEBUG [Admin]: Ambassadors Count: {len(ambassadors)}")
    except Exception as e:
        print(f"[ADMIN] Failed to load ambassadors: {e}")
        return JSONResponse({"error": f"Failed to load ambassadors: {str(e)}"}, status_code=500)

    # Step 2: Fetch worker counts per ambassador (safe separate query)
    if ambassadors:
        try:
            counts_resp = await supabase_request("GET",
                "workers?select=ambassador_id&ambassador_id=not.is.null")
            if counts_resp.is_success:
                workers_raw = counts_resp.json()
                count_map = {}
                for w in workers_raw:
                    aid = w.get("ambassador_id")
                    if aid:
                        count_map[aid] = count_map.get(aid, 0) + 1
                # Inject worker_count into each ambassador record
                for amb in ambassadors:
                    amb["worker_count"] = count_map.get(amb["id"], 0)
        except Exception as e:
            print(f"[ADMIN] Worker counts query failed (likely PostgREST schema cache delay): {str(e)[:100]}")
            # Silently degrade: set counts to 0 so the table still loads
            for amb in ambassadors:
                amb["worker_count"] = 0

    return JSONResponse(ambassadors)

@app.post("/api/admin/ambassadors")
async def admin_create_ambassador(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    area: str = Form(...),
    password: str = Form(...)
):
    """Create or update an ambassador account using SHA-256 hashing."""
    phone = phone.strip().replace(" ", "")
    hashed = _amb_hash(password)   # clean 64-char hex — guaranteed to save to TEXT column
    print(f"[AMB CREATE] phone={phone} hash={hashed[:12]}...")

    # Check existing
    existing = await supabase_request("GET", f"ambassadors?phone=eq.{phone}&select=id,name")
    existing_list = existing.json()

    if existing_list:
        print(f"[AMB CREATE] Updating existing: {existing_list[0].get('name')}")
        patch = await _supabase_admin_patch(f"ambassadors?phone=eq.{phone}", {
            "name":          name.strip(),
            "area":          area.strip(),
            "password_hash": hashed,
            "status":        "active",
        })
        if patch.status_code in (200, 201, 204):
            patch_data = patch.json() if patch.status_code != 204 else []
            if patch_data:
                return JSONResponse({"status": "success", "message": "Ambassador password updated. They can now login."})
            else:
                print(f"[AMB CREATE] RLS silent failure? Patch returned empty for {phone}")
                return JSONResponse({"status": "error", "message": "Update failed (Check RLS write permissions). Please use SQL editor to update."})
        return JSONResponse({"status": "error", "message": f"DB update failed: {patch.text[:100]}"})

    # New ambassador
    import secrets
    ref_code = f"NEARVA-{phone[-4:]}-{secrets.token_hex(2).upper()}" 
    post = await _supabase_admin_post("ambassadors", {
        "name":          name.strip(),
        "phone":         phone,
        "area":          area.strip(),
        "referral_code": ref_code,
        "password_hash": hashed,
        "status":        "active",
    })
    if post.status_code in (200, 201):
        post_data = post.json() if post.status_code != 204 else []
        if post_data:
            return JSONResponse({"status": "success", "message": f"Ambassador created with code: {ref_code}"})
        else:
            print(f"[AMB CREATE] RLS silent failure? Post returned empty for {phone}")
            return JSONResponse({"status": "error", "message": "Create failed (Check RLS write permissions). Please use SQL editor."})
    return JSONResponse({"status": "error", "message": f"DB create failed: {post.text[:100]}"})

@app.post("/api/admin/ambassadors/{amb_id}/toggle")
async def admin_toggle_ambassador(amb_id: str, request: Request):
    resp = await supabase_request("GET", f"ambassadors?id=eq.{amb_id}&select=status")
    rows = resp.json()
    if not rows:
        return JSONResponse({"status": "error", "message": "Ambassador not found"}, status_code=404)
    new_status = "inactive" if rows[0]["status"] == "active" else "active"
    await supabase_request("PATCH", f"ambassadors?id=eq.{amb_id}", data={"status": new_status})
    return JSONResponse({"status": "success", "new_status": new_status})

@app.delete("/api/admin/ambassadors/{amb_id}")
async def admin_delete_ambassador(amb_id: str, request: Request):
    resp = await supabase_request("GET", f"ambassadors?id=eq.{amb_id}&select=id")
    if not resp.json():
        return JSONResponse({"status": "error", "message": "Ambassador not found"}, status_code=404)
    await supabase_request("DELETE", f"ambassadors?id=eq.{amb_id}")
    return JSONResponse({"status": "success"})


# Simple in-memory caching for optimization
_API_CACHE = {}

def _get_cache(key: str, ttl: int = 60):
    val = _API_CACHE.get(key)
    if val and (time.time() - val['time']) < ttl:
        return val['data']
    return None

def _set_cache(key: str, data):
    _API_CACHE[key] = {'time': time.time(), 'data': data}


# ── Public: Ambassador list (used by register page dropdown) ────────────────

@app.get("/api/ambassadors")
async def public_ambassadors_list():
    """Lightweight endpoint for registration dropdowns."""
    cache_key = "public_ambassadors"
    cached = _get_cache(cache_key, ttl=300)
    if cached: return JSONResponse(cached)
    
    resp = await supabase_request("GET", "ambassadors?select=id,name,area&status=eq.active&order=name.asc")
    data = resp.json() if resp.is_success else []
    _set_cache(cache_key, data)
    return JSONResponse(data)

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    if not request.session.get("admin_logged_in"):
        print("DEBUG [Admin]: User list requested but admin not logged in session.")
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    resp = await supabase_request("GET", "users?select=*&order=created_at.desc")
    print(f"DEBUG [Admin]: Users Fetch Status: {resp.status_code}")
    
    if not resp.is_success:
        print(f"DEBUG [Admin]: Users Fetch Error: {resp.text}")
        return []
        
    data = resp.json()
    print(f"DEBUG [Admin]: Users Count: {len(data)}")
    return data

@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error"}, status_code=401)
    await supabase_request("DELETE", f"users?id=eq.{user_id}")
    await notify_admin('user_deleted', {"id": user_id})
    return {"status": "success"}

@app.get("/api/admin/analytics")
async def admin_analytics(request: Request):
    """Real-time platform analytics with growth indicators and time-series data."""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    
    cached = _get_cache("admin_analytics", ttl=5) # 5 seconds cache
    if cached: return JSONResponse(cached)

    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        last_week = now - timedelta(days=7)

        # 1. Fetch Basic Data
        print("DEBUG: Fetching basic analytics data...")
        workers_resp = await supabase_request("GET", "workers?select=id,verification_status,is_online,created_at")
        users_resp = await supabase_request("GET", "users?select=id,created_at")
        amb_resp = await supabase_request("GET", "ambassadors?select=id,status")
        jobs_resp = await supabase_request("GET", "job_requests?select=id,status,created_at")

        workers = workers_resp.json() if workers_resp.status_code == 200 else []
        users = users_resp.json() if users_resp.status_code == 200 else []
        ambs = amb_resp.json() if amb_resp.status_code == 200 else []
        jobs = jobs_resp.json() if jobs_resp.status_code == 200 else []

        def parse_date(date_str):
            if not date_str: return None
            try:
                # Handle various formats (Supabase typically uses ISO with or without Z)
                clean_str = date_str.replace('Z', '+00:00').split('.')[0] # Strip microseconds for simpler parsing
                if '+' not in clean_str: clean_str += '+00:00'
                return datetime.fromisoformat(clean_str)
            except:
                return None

        def get_growth(items, start_date, end_date):
            current = 0
            previous = 0
            for item in items:
                created = parse_date(item.get('created_at'))
                if not created: continue
                if start_date <= created <= end_date:
                    current += 1
                elif (start_date - timedelta(days=7)) <= created <= start_date:
                    previous += 1
            if previous == 0: return 0
            return round(((current - previous) / previous) * 100, 1)

        # 2. Daily Time Series
        chart_labels = []
        chart_data_users = []
        chart_data_workers = []
        
        print("DEBUG: Generating chart data...")
        for i in range(13, -1, -1):
            target_date = (now - timedelta(days=i)).date()
            chart_labels.append(target_date.strftime("%b %d"))
            
            u_count = sum(1 for u in users if (parse_date(u.get('created_at')) or now).date() == target_date)
            w_count = sum(1 for w in workers if (parse_date(w.get('created_at')) or now).date() == target_date)
            
            chart_data_users.append(u_count)
            chart_data_workers.append(w_count)

        # 3. Revenue Simulation
        print("DEBUG: Calculating revenue...")
        completed_jobs = len([j for j in jobs if j.get('status') == 'completed'])
        total_revenue = completed_jobs * 100
        
        data = {
            "total_users": len(users),
            "total_workers": len(workers),
            "approved_workers": len([w for w in workers if w.get("verification_status") in ("VERIFIED", "Approved")]),
            "online_workers": len([w for w in workers if w.get("is_online")]),
            "active_jobs": len([j for j in jobs if j.get("status") in ("accepted", "in_progress")]),
            "total_jobs": len(jobs),
            "total_ambassadors": len(ambs),
            "active_ambassadors": len([a for a in ambs if a.get("status") == "active"]),
            "revenue": {
                "total": total_revenue,
                "worker_share": total_revenue * 0.7,
                "admin_share": total_revenue * 0.2,
                "amb_share": total_revenue * 0.1,
                "completed_count": completed_jobs
            },
            "growth": {
                "users": get_growth(users, last_week, now),
                "workers": get_growth(workers, last_week, now),
                "jobs": get_growth(jobs, last_week, now)
            },
            "chart": {
                "labels": chart_labels,
                "user_counts": chart_data_users,
                "worker_counts": chart_data_workers
            }
        }
        _set_cache("admin_analytics", data)
        return JSONResponse(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Analytics error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/{city}/{service}", response_class=HTMLResponse)
async def service_city_page(request: Request, city: str, service: str):
    return templates.TemplateResponse(
        "service_city.html",
        {
            "request": request,
            "city": city.capitalize(),
            "service": service.replace("-", " ").title()
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

