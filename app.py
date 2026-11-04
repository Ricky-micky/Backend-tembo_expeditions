# app.py - Complete Backend with Admin Hierarchy, Reviews, Packages, Excursions, Transfers, Blog, About, Community Stories, Parks
from flask import Flask, request, jsonify, g, send_from_directory, current_app
from flask_mail import Mail, Message
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
from functools import wraps
from dotenv import load_dotenv
import os
import logging
import re
import json
import jwt
import uuid
import mimetypes
from urllib.parse import urlparse
from html import escape
import cloudinary
import cloudinary.uploader
from werkzeug.utils import secure_filename
from logging.handlers import RotatingFileHandler

from config import Config, DevelopmentConfig, ProductionConfig, TestingConfig
from extensions import db, migrate

from models import (
    User, AdminRole, AdminActionLog, ImpersonationSession,
    CustomerReview, ReviewMedia, ReviewReply,
    Package, PackageCategory, PackageDay, PackageMedia,
    Excursion, ExcursionMedia,
    Transfer,
    BlogPost,
    AboutImage, AboutPerson,
    Comment, Story, Park
)

UTC = timezone.utc
MAX_EXCURSION_MEDIA = 4

# Logo is embedded INSIDE each email (CID inline attachment) so it always shows,
# even when the email client blocks remote images or the website is unreachable.
# Put the file at: <backend folder>/static/joz-logo-email.png
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "joz-logo-email.png")
LOGO_CID = "joz-logo"

try:
    from importlib.metadata import version as _pkg_version
    _FLASK_MAIL_DICT_HEADERS = tuple(int(x) for x in _pkg_version("Flask-Mail").split(".")[:2]) >= (0, 10)
except Exception:
    _FLASK_MAIL_DICT_HEADERS = True
# Fallback only used if the local file is missing
LOGO_URL = "https://temboexpedition.vercel.app/joz-logo.png"

def _parse_origins():
    raw = os.environ.get('ALLOWED_ORIGINS', '')
    origins = [o.strip() for o in raw.split(',') if o.strip()]
    defaults = [
        'http://localhost:5173', 'http://127.0.0.1:5173',
        'http://localhost:3000', 'http://127.0.0.1:3000',
        # Adding both spellings to be safe
        'https://temboexpedition.vercel.app',
        'https://temboexpedition.vercel.app',
        'https://backend-tembo-expeditions.onrender.com',
    ]
    for d in defaults:
        if d not in origins:
            origins.append(d)
    return origins

def _cors_regex():
    allowed = _parse_origins()
    escaped = [re.escape(o) for o in allowed]
    # The [s]? makes the 's' optional, catching both spellings and all Vercel preview URLs
    escaped.append(r'https://temboexpedition[s]?.*\.vercel\.app')
    return r'^(' + '|'.join(escaped) + r')$'

# ============================================================
# HTML EMAIL TEMPLATE HELPER
# ============================================================
def generate_email_template(title, content_html, footer_message="Joz Tembo Tours & Safari", logo_src=None):
    logo_src = logo_src or LOGO_URL
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 20px; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            .header {{ background-color: #fdf9eb; padding: 25px 20px; text-align: center; border-bottom: 3px solid #d4af37; }}
            .header img {{ max-height: 140px; width: auto; max-width: 100%; }}
            .header h1 {{ color: #5a2d0c; margin: 15px 0 0 0; font-size: 22px; font-weight: 500; letter-spacing: 1px; }}
            .content {{ padding: 30px; line-height: 1.6; font-size: 15px; }}
            .highlight-box {{ background-color: #f8f9fa; border-left: 4px solid #d4af37; padding: 15px; margin: 20px 0; border-radius: 4px; }}
            .highlight-box strong {{ color: #1a1a1a; }}
            .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; font-size: 12px; color: #666; border-top: 1px solid #eeeeee; }}
            .footer img {{ max-height: 50px; opacity: 0.7; margin-bottom: 10px; }}
            .btn {{ display: inline-block; padding: 10px 20px; background-color: #d4af37; color: #ffffff; text-decoration: none; border-radius: 4px; font-weight: bold; margin-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <img src="{logo_src}" alt="Joz Tembo Tours Logo" width="260" style="display:block;margin:0 auto;height:auto;border:0;">
                <h1>{title}</h1>
            </div>
            <div class="content">
                {content_html}
            </div>
            <div class="footer">
                <img src="{logo_src}" alt="Joz Tembo Tours" width="110" style="display:block;margin:0 auto 10px;height:auto;border:0;">
                <p>{footer_message}<br>
                Thank you for choosing us!<br>
                &copy; {datetime.now().year} Joz Tembo Tours. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

def set_email_html(msg, title, content_html, footer_message="Joz Tembo Tours & Safari"):
    """Build the branded HTML and embed the logo into the message."""
    logo_src = None
    try:
        with open(LOGO_PATH, "rb") as f:
            logo_bytes = f.read()
        # Flask-Mail >= 0.10 wants headers as a dict; older versions want a list of tuples
        logo_headers = {"Content-ID": f"<{LOGO_CID}>", "X-Attachment-Id": LOGO_CID}
        if not _FLASK_MAIL_DICT_HEADERS:
            logo_headers = list(logo_headers.items())
        msg.attach("joz-logo.png", "image/png", logo_bytes, "inline", headers=logo_headers)
        logo_src = f"cid:{LOGO_CID}"
    except OSError:
        logging.getLogger(__name__).warning("Email logo not found at %s - using URL fallback", LOGO_PATH)
    msg.html = generate_email_template(title, content_html, footer_message, logo_src=logo_src)
    return msg

def create_app(config_class=None):
    app = Flask(__name__)

    if config_class:
        app.config.from_object(config_class)
    else:
        env = os.environ.get('FLASK_ENV', 'development')
        if env == 'production':
            app.config.from_object(ProductionConfig)
        elif env == 'testing':
            app.config.from_object(TestingConfig)
        else:
            app.config.from_object(DevelopmentConfig)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', app.config.get('SECRET_KEY'))
    app.config['JWT_EXPIRATION_HOURS'] = 168

    configured_upload_folder = os.environ.get('UPLOAD_FOLDER') or app.config.get('UPLOAD_FOLDER') or 'uploads'
    app.config['UPLOAD_FOLDER'] = os.path.abspath(configured_upload_folder)
    upload_base = app.config['UPLOAD_FOLDER']
    # NOTE: 'parks' added for forward-compat. Existing folders unchanged.
    for folder in ['reviews', 'packages', 'excursions', 'avatars', 'blog', 'about', 'parks']:
        os.makedirs(os.path.join(upload_base, folder), exist_ok=True)

    cloudinary_cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME')
    cloudinary_api_key = os.environ.get('CLOUDINARY_API_KEY')
    cloudinary_api_secret = os.environ.get('CLOUDINARY_API_SECRET')
    app.config['CLOUDINARY_ENABLED'] = bool(
        cloudinary_cloud_name and cloudinary_api_key and cloudinary_api_secret
    )

    if app.config['CLOUDINARY_ENABLED']:
        cloudinary.config(
            cloud_name=cloudinary_cloud_name,
            api_key=cloudinary_api_key,
            api_secret=cloudinary_api_secret,
            secure=True
        )

    db.init_app(app)
    migrate.init_app(app, db)

    # --- UPDATED CORS CONFIGURATION ---
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": _cors_regex(),
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "allow_headers": ["Content-Type", "Authorization"],
                "expose_headers": ["Content-Type"],
                "supports_credentials": False,
                "max_age": 3600,
            },
            r"/uploads/*": {
                "origins": "*", # Uploads can be public
                "methods": ["GET", "OPTIONS"],
                "allow_headers": ["Content-Type"],
            },
        },
    )

    @app.before_request
    def _enforce_https():
        if app.debug:
            return None
        host = request.host.split(':')[0]
        if host in ('localhost', '127.0.0.1'):
            return None
        if (
            os.environ.get('FLASK_ENV') == 'production'
            and not request.is_secure
            and request.headers.get('X-Forwarded-Proto', 'http') != 'https'
        ):
            url = request.url.replace('http://', 'https://', 1)
            return jsonify({'success': False, 'error': 'HTTPS required', 'redirect': url}), 426
        return None

    mail = Mail(app)

    @app.route("/api/health", methods=["GET"])
    def _health_early():
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now(UTC).isoformat(),
            "environment": os.environ.get('FLASK_ENV', 'development'),
            "cors_origins": _parse_origins(),
            "mail_configured": bool(app.config.get('MAIL_SERVER')), # Fixed missing closing parenthesis here
            "cloudinary_configured": bool(app.config.get('CLOUDINARY_ENABLED')),
        }), 200

    setup_logging(app)
    register_routes(app, mail)

    with app.app_context():
        try:
            db.create_all()
            if PackageCategory.query.count() == 0:
                categories = [
                    PackageCategory(name='Normal Packages', slug='normal', description='Standard safari packages', display_order=1),
                    PackageCategory(name='Best Combo', slug='best-combo', description='Best value combination packages', display_order=2),
                    PackageCategory(name='Premium', slug='premium', description='Luxury premium packages', display_order=3),
                    PackageCategory(name='Budget', slug='budget', description='Affordable safari options', display_order=4),
                ]
                for cat in categories:
                    db.session.add(cat)
                db.session.commit()
            app.logger.info("✅ Database tables created/verified successfully")
        except Exception as e:
            app.logger.error(f"❌ Database initialization error: {e}")

    return app

def setup_logging(app):
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/safari_booking.log', maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    console_handler.setLevel(logging.INFO)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('🦁 Joztembo Tours Backend Startup')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'no_token'}), 401
        user = User.verify_auth_token(token)
        if not user:
            return jsonify({'success': False, 'error': 'Invalid or expired token', 'code': 'invalid_token'}), 401
        if not user.is_active:
            return jsonify({'success': False, 'error': 'Account has been deactivated', 'code': 'inactive'}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'no_token'}), 401
        user = User.verify_auth_token(token)
        if not user:
            return jsonify({'success': False, 'error': 'Invalid or expired token', 'code': 'invalid_token'}), 401
        if not user.is_admin:
            return jsonify({'success': False, 'error': 'Admin access required.', 'code': 'not_admin', 'hint': 'Log out and log back in with an admin account.'}), 403
        if not user.is_active:
            return jsonify({'success': False, 'error': 'Account has been deactivated', 'code': 'inactive'}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'no_token'}), 401
        user = User.verify_auth_token(token)
        if not user:
            return jsonify({'success': False, 'error': 'Invalid or expired token', 'code': 'invalid_token'}), 401
        if not user.is_admin:
            return jsonify({'success': False, 'error': 'Admin access required.', 'code': 'not_admin'}), 403
        if not user.is_super_admin():
            return jsonify({'success': False, 'error': 'Super admin access required.', 'code': 'not_super_admin', 'your_role': user.admin_role}), 403
        if not user.is_active:
            return jsonify({'success': False, 'error': 'Account has been deactivated', 'code': 'inactive'}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission_method):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            if not token:
                return jsonify({'success': False, 'error': 'Authentication required', 'code': 'no_token'}), 401
            user = User.verify_auth_token(token)
            if not user:
                return jsonify({'success': False, 'error': 'Invalid or expired token', 'code': 'invalid_token'}), 401
            if not user.is_active:
                return jsonify({'success': False, 'error': 'Account has been deactivated', 'code': 'inactive'}), 403
            if not user.is_admin:
                return jsonify({'success': False, 'error': 'Admin access required.', 'code': 'not_admin'}), 403
            permission_func = getattr(user, permission_method, None)
            if not permission_func or not permission_func():
                return jsonify({'success': False, 'error': f'Permission denied: {permission_method}', 'code': 'permission_denied', 'your_role': user.admin_role, 'required_permission': permission_method}), 403
            g.current_user = user
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def optional_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        g.current_user = None
        if token:
            user = User.verify_auth_token(token)
            if user and user.is_active:
                g.current_user = user
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def save_uploaded_file(file, folder, allowed_extensions):
    if not file or not file.filename:
        return None, None
    if not allowed_file(file.filename, allowed_extensions):
        return None, None

    original_filename = secure_filename(file.filename)
    ext = original_filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    mime_type, _ = mimetypes.guess_type(original_filename)

    if current_app.config.get('CLOUDINARY_ENABLED'):
        try:
            result = cloudinary.uploader.upload(
                file.stream,
                folder=f"joztembo/{folder}",
                public_id=uuid.uuid4().hex,
                resource_type='auto',
                overwrite=False,
                unique_filename=False
            )
            file_url = result.get('secure_url')
            if not file_url:
                raise RuntimeError('Cloudinary did not return a secure image URL')

            return file_url, {
                'file_name': original_filename,
                'file_size': result.get('bytes', 0),
                'mime_type': mime_type,
                'public_id': result.get('public_id'),
                'resource_type': result.get('resource_type')
            }
        except Exception as e:
            current_app.logger.exception("Cloudinary upload failed")
            raise RuntimeError(f"Cloudinary upload failed: {str(e)}") from e

    upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], folder)
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, unique_filename)
    file.save(filepath)
    file_size = os.path.getsize(filepath)
    file_url = f"/uploads/{folder}/{unique_filename}"

    return file_url, {
        'file_name': original_filename,
        'file_size': file_size,
        'mime_type': mime_type
    }


def get_upload_file_path(file_url):
    if not file_url or not file_url.startswith('/uploads/'):
        return None
    relative_path = file_url[len('/uploads/'):]
    path_parts = relative_path.split('/')
    if len(path_parts) != 2 or any(part in ('', '.', '..') for part in path_parts):
        return None
    return os.path.join(current_app.config['UPLOAD_FOLDER'], *path_parts)


def delete_uploaded_file(file_url):
    if not file_url:
        return False

    if file_url.startswith('/uploads/'):
        file_path = get_upload_file_path(file_url)
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
            return True
        return False

    parsed_url = urlparse(file_url)
    if parsed_url.netloc.lower() not in ('res.cloudinary.com', 'www.res.cloudinary.com'):
        return False

    if not current_app.config.get('CLOUDINARY_ENABLED'):
        current_app.logger.warning("Cloudinary credentials are missing; remote media was not deleted")
        return False

    try:
        path = parsed_url.path
        if '/upload/' not in path:
            return False

        uploaded_path = path.split('/upload/', 1)[1]
        path_parts = uploaded_path.split('/')
        if path_parts and re.fullmatch(r'v\\d+', path_parts[0]):
            path_parts = path_parts[1:]
        if not path_parts:
            return False

        public_id_with_extension = '/'.join(path_parts)
        public_id = re.sub(r'\\.[^.]+$', '', public_id_with_extension)
        resource_type = 'video' if '/video/upload/' in path else 'image'

        result = cloudinary.uploader.destroy(
            public_id,
            resource_type=resource_type,
            invalidate=True
        )
        return result.get('result') == 'ok'
    except Exception:
        current_app.logger.exception("Cloudinary media deletion failed")
        return False


def get_media_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    image_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'tiff', 'ico'}
    video_exts = {'mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'webm', 'm4v', '3gp'}
    if ext in image_exts: return 'image'
    elif ext in video_exts: return 'video'
    return None


# ------------------------------------------------------------------
# PARK HELPERS
# ------------------------------------------------------------------
def _slugify(text):
    slug = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return slug or f"park-{uuid.uuid4().hex[:8]}"


def _unique_park_slug(base_slug, ignore_id=None):
    slug = base_slug
    i = 1
    q = Park.query.filter_by(slug=slug)
    if ignore_id is not None:
        q = q.filter(Park.id != ignore_id)
    while q.first():
        slug = f"{base_slug}-{i}"
        i += 1
        q = Park.query.filter_by(slug=slug)
        if ignore_id is not None:
            q = q.filter(Park.id != ignore_id)
    return slug


def _coerce_park_list(value):
    """Accept JSON list, JSON string, or newline/comma string → list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        # Fallback: split on newlines
        return [line.strip() for line in value.split('\n') if line.strip()]
    return []


def _coerce_animal_stories(value):
    """Accept list of {name, story}, JSON string, or 'Name :: Story' lines."""
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict) and item.get('name') and item.get('story'):
                out.append({'name': str(item['name']), 'story': str(item['story'])})
        return out
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return _coerce_animal_stories(parsed)
        except Exception:
            pass
        out = []
        for line in value.split('\n'):
            if '::' in line:
                name, story = line.split('::', 1)
                if name.strip() and story.strip():
                    out.append({'name': name.strip(), 'story': story.strip()})
        return out
    return []


def _parse_park_payload():
    """Parse JSON or multipart request into (payload, hero_file, gallery_files)."""
    if request.content_type and 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        hero_file = request.files.get('hero_image')
        gallery_files = request.files.getlist('gallery')
    else:
        data = request.get_json(silent=True) or {}
        hero_file, gallery_files = None, []

    def _num(key, cast):
        v = data.get(key)
        if v in (None, '', 'null'):
            return None
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None

    payload = {
        'name': (data.get('name') or '').strip(),
        'location': (data.get('location') or '').strip(),
        'tagline': (data.get('tagline') or '').strip(),
        'description': data.get('description') or '',
        'history': data.get('history') or '',
        'wildlife': _coerce_park_list(data.get('wildlife')),
        'animal_stories': _coerce_animal_stories(data.get('animal_stories')),
        'best_time_to_visit': (data.get('best_time_to_visit') or '').strip(),
        'area_km2': _num('area_km2', float),
        'established_year': _num('established_year', int),
        'display_order': _num('display_order', int) or 0,
        'is_active': str(data.get('is_active', 'true')).lower() in ('true', '1', 'yes', 'on'),
    }
    # Optional pre-existing gallery list (list of URLs) when not uploading new files
    incoming_gallery = _coerce_park_list(data.get('gallery'))
    return payload, hero_file, gallery_files, incoming_gallery


def register_routes(app, mail):
    
    @app.route("/api/test-email", methods=["GET"])
    def test_email():
        try:
            msg = Message(subject="TEST EMAIL FROM JOZTEMBO TOURS", recipients=["tembo4401@gmail.com"])
            set_email_html(
                msg,
                "Test Email Successful!",
                "<p>Congratulations! Your HTML emails with the logo are working perfectly.</p>"
            )
            mail.send(msg)
            return "<h1>SUCCESS! The styled email was sent. Check your inbox.</h1>", 200
        except Exception as e:
            return f"<h1>FAILED TO SEND EMAIL</h1><p>Error: {str(e)}</p>", 500

    @app.route('/uploads/<path:folder>/<path:filename>')
    def serve_upload(folder, filename):
        # 'parks' added for forward-compat; existing folders unchanged.
        allowed_folders = {'reviews', 'packages', 'excursions', 'avatars', 'blog', 'about', 'parks'}
        if folder not in allowed_folders:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], folder)
        response = send_from_directory(upload_folder, filename, conditional=True, max_age=86400)
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    # ================= AUTH =================
    @app.route("/api/auth/register", methods=["POST"])
    def register():
        try:
            data = request.get_json()
            if not data.get('name') or not data.get('email') or not data.get('password'):
                return jsonify({'success': False, 'error': 'Name, email, and password are required'}), 400
            email_pattern = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
            if not email_pattern.match(data['email']):
                return jsonify({'success': False, 'error': 'Invalid email format'}), 400
            if len(data['password']) < 6:
                return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
            email = data['email'].lower().strip()
            if User.query.filter_by(email=email).first():
                return jsonify({'success': False, 'error': 'Email already registered'}), 400
            base_username = email.split('@')[0]
            username = base_username
            counter = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
            user = User(name=data['name'].strip(), email=email, username=username, phone=data.get('phone', '').strip(), is_admin=False, admin_role=None)
            user.set_password(data['password'])
            db.session.add(user)
            db.session.commit()
            token = user.generate_auth_token()
            return jsonify({'success': True, 'token': token, 'user': user.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': 'Registration failed'}), 500

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        try:
            data = request.get_json()
            if not data.get('email') or not data.get('password'):
                return jsonify({'success': False, 'error': 'Email and password are required'}), 400
            login_id = data['email'].lower().strip()
            user = User.query.filter(db.or_(User.email == login_id, User.username == login_id)).first()
            if not user or not user.check_password(data['password']):
                return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
            if not user.is_active:
                return jsonify({'success': False, 'error': 'Account has been deactivated'}), 403
            token = user.generate_auth_token()
            return jsonify({'success': True, 'token': token, 'user': user.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': 'Login failed'}), 500

    @app.route("/api/auth/me", methods=["GET"])
    @login_required
    def get_current_user():
        return jsonify({'success': True, 'user': g.current_user.to_dict()}), 200

    @app.route("/api/admin/setup", methods=["POST"])
    def setup_super_admin():
        try:
            existing_admin = User.query.filter_by(is_admin=True).first()
            if existing_admin:
                return jsonify({'success': False, 'error': 'Admin already exists. Use admin management endpoints.'}), 403
            data = request.get_json()
            if not data.get('name') or not data.get('email') or not data.get('password'):
                return jsonify({'success': False, 'error': 'Name, email, and password are required'}), 400
            email = data['email'].lower().strip()
            if User.query.filter_by(email=email).first():
                return jsonify({'success': False, 'error': 'Email already registered'}), 400
            base_username = email.split('@')[0]
            username = base_username
            counter = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
            super_admin = User(name=data['name'].strip(), email=email, username=username, phone=data.get('phone', '').strip(), is_admin=True, admin_role=AdminRole.SUPER_ADMIN, admin_order=1)
            super_admin.set_password(data['password'])
            db.session.add(super_admin)
            db.session.commit()
            token = super_admin.generate_auth_token()
            return jsonify({'success': True, 'message': 'Super admin created successfully', 'token': token, 'user': super_admin.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= ADMIN MANAGEMENT =================
    @app.route("/api/admin/admins", methods=["GET"])
    @super_admin_required
    def get_all_admins():
        try:
            admins = User.query.filter_by(is_admin=True).order_by(User.admin_order).all()
            return jsonify({'success': True, 'data': [admin.to_admin_dict() for admin in admins]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/admins", methods=["POST"])
    @super_admin_required
    def create_admin():
        try:
            data = request.get_json()
            if not data.get('name') or not data.get('email') or not data.get('password'):
                return jsonify({'success': False, 'error': 'Name, email, and password are required'}), 400
            if not data.get('admin_role') or data['admin_role'] not in AdminRole.ALL:
                return jsonify({'success': False, 'error': f'Valid admin_role required: {AdminRole.ALL}'}), 400
            if data['admin_role'] == AdminRole.SUPER_ADMIN:
                return jsonify({'success': False, 'error': 'Cannot create another super admin'}), 400
            email = data['email'].lower().strip()
            if User.query.filter_by(email=email).first():
                return jsonify({'success': False, 'error': 'Email already registered'}), 400
            base_username = email.split('@')[0]
            username = base_username
            counter = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
            max_order = db.session.query(db.func.max(User.admin_order)).filter_by(is_admin=True).scalar() or 1
            next_order = max_order + 1
            new_admin = User(name=data['name'].strip(), email=email, username=username, phone=data.get('phone', '').strip(), is_admin=True, admin_role=data['admin_role'], admin_order=next_order, created_by_id=g.current_user.id)
            new_admin.set_password(data['password'])
            db.session.add(new_admin)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='create', resource_type='admin', resource_id=new_admin.id, details={'admin_role': data['admin_role'], 'email': email}, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': f'Admin created with role: {data["admin_role"]}', 'data': new_admin.to_admin_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/admins/<int:admin_id>", methods=["PUT"])
    @super_admin_required
    def update_admin(admin_id):
        try:
            admin = User.query.get(admin_id)
            if not admin or not admin.is_admin:
                return jsonify({'success': False, 'error': 'Admin not found'}), 404
            if admin.is_super_admin():
                return jsonify({'success': False, 'error': 'Cannot modify super admin'}), 403
            data = request.get_json()
            if 'name' in data: admin.name = data['name'].strip()
            if 'phone' in data: admin.phone = data['phone'].strip()
            if 'admin_role' in data and data['admin_role'] in AdminRole.ALL:
                if data['admin_role'] == AdminRole.SUPER_ADMIN:
                    return jsonify({'success': False, 'error': 'Cannot promote to super admin'}), 400
                admin.admin_role = data['admin_role']
            if 'is_active' in data: admin.is_active = data['is_active']
            admin.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='update', resource_type='admin', resource_id=admin_id, details=data, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Admin updated', 'data': admin.to_admin_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/admins/<int:admin_id>", methods=["DELETE"])
    @super_admin_required
    def delete_admin(admin_id):
        try:
            admin = User.query.get(admin_id)
            if not admin or not admin.is_admin:
                return jsonify({'success': False, 'error': 'Admin not found'}), 404
            if admin.is_super_admin():
                return jsonify({'success': False, 'error': 'Cannot delete super admin'}), 403
            if admin.id == g.current_user.id:
                return jsonify({'success': False, 'error': 'Cannot delete yourself'}), 400
            admin.is_active = False
            admin.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='admin', resource_id=admin_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Admin deactivated'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/impersonate/<int:user_id>", methods=["POST"])
    @super_admin_required
    def impersonate_user(user_id):
        try:
            target_user = User.query.get(user_id)
            if not target_user:
                return jsonify({'success': False, 'error': 'User not found'}), 404
            if not target_user.is_active:
                return jsonify({'success': False, 'error': 'Cannot impersonate inactive user'}), 400
            if target_user.id == g.current_user.id:
                return jsonify({'success': False, 'error': 'Cannot impersonate yourself'}), 400
            token = target_user.generate_auth_token()
            session = ImpersonationSession(super_admin_id=g.current_user.id, impersonated_user_id=target_user.id, token=token, is_active=True)
            db.session.add(session)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='impersonate', resource_type='user', resource_id=user_id, details={'target_email': target_user.email, 'target_role': target_user.admin_role}, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': f'Now impersonating {target_user.name}', 'token': token, 'user': target_user.to_dict(), 'impersonating': True}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/stop-impersonation", methods=["POST"])
    @super_admin_required
    def stop_impersonation():
        try:
            sessions = ImpersonationSession.query.filter_by(super_admin_id=g.current_user.id, is_active=True).all()
            for session in sessions:
                session.is_active = False
                session.ended_at = datetime.now(UTC)
            db.session.commit()
            token = g.current_user.generate_auth_token()
            return jsonify({'success': True, 'message': 'Impersonation ended', 'token': token, 'user': g.current_user.to_dict(), 'impersonating': False}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/impersonation-sessions", methods=["GET"])
    @super_admin_required
    def get_impersonation_sessions():
        try:
            sessions = ImpersonationSession.query.order_by(ImpersonationSession.started_at.desc()).limit(50).all()
            return jsonify({'success': True, 'data': [s.to_dict() for s in sessions]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/logs", methods=["GET"])
    @admin_required
    def get_admin_logs():
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 50, type=int)
            pagination = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            return jsonify({'success': True, 'data': [log.to_dict() for log in pagination.items], 'pagination': {'page': page, 'per_page': per_page, 'total': pagination.total, 'pages': pagination.pages}}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= REVIEWS =================
    @app.route("/api/reviews", methods=["GET"])
    def get_reviews():
        try:
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 10, type=int)
            sort_by = request.args.get('sort_by', 'created_at')
            order = request.args.get('order', 'desc')
            status = request.args.get('status', 'approved')
            query = CustomerReview.query.filter_by(is_active=True)
            if status and status != 'all':
                query = query.filter_by(status=status)
            if sort_by == 'rating':
                query = query.order_by(CustomerReview.rating.desc() if order == 'desc' else CustomerReview.rating.asc())
            else:
                query = query.order_by(CustomerReview.created_at.desc() if order == 'desc' else CustomerReview.created_at.asc())
            pagination = query.paginate(page=page, per_page=per_page, error_out=False)
            reviews = pagination.items
            avg_rating = db.session.query(db.func.avg(CustomerReview.rating)).filter(CustomerReview.is_active == True, CustomerReview.status == 'approved').scalar()
            return jsonify({'success': True, 'data': [review.to_dict() for review in reviews], 'pagination': {'page': page, 'per_page': per_page, 'total': pagination.total, 'pages': pagination.pages, 'has_next': pagination.has_next, 'has_prev': pagination.has_prev}, 'stats': {'total_reviews': pagination.total, 'average_rating': round(float(avg_rating), 1) if avg_rating else 0}}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/reviews", methods=["POST"])
    @optional_auth
    def create_review():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            rating = int(data.get('rating', 0))
            if not 1 <= rating <= 5:
                return jsonify({'success': False, 'error': 'Rating must be between 1 and 5'}), 400
            if not data.get('title') or not data.get('content'):
                return jsonify({'success': False, 'error': 'Title and content are required'}), 400
            user_id = None
            reviewer_name = data.get('reviewer_name', '').strip()
            reviewer_email = data.get('reviewer_email', '').strip()
            if g.current_user:
                user_id = g.current_user.id
                reviewer_name = g.current_user.name
                reviewer_email = g.current_user.email
            elif not reviewer_name:
                reviewer_name = 'Anonymous'
            visit_date = None
            if data.get('visit_date'):
                try:
                    visit_date = datetime.strptime(data['visit_date'], '%Y-%m-%d').date()
                except ValueError:
                    return jsonify({'success': False, 'error': 'Invalid visit date format'}), 400
            review = CustomerReview(user_id=user_id, reviewer_name=reviewer_name, reviewer_email=reviewer_email, rating=rating, title=data['title'].strip(), content=data['content'].strip(), visit_date=visit_date, package_used=data.get('package_used', '').strip(), status='approved')
            db.session.add(review)
            db.session.flush()
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'reviews', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = ReviewMedia(review_id=review.id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], uploaded_by_id=user_id)
                        db.session.add(media)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Review submitted successfully', 'data': review.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/reviews/<int:review_id>", methods=["PUT"])
    @login_required
    def update_review(review_id):
        try:
            review = CustomerReview.query.get(review_id)
            if not review or not review.is_active:
                return jsonify({'success': False, 'error': 'Review not found'}), 404
            data = request.get_json()
            if 'rating' in data:
                if not 1 <= data['rating'] <= 5:
                    return jsonify({'success': False, 'error': 'Rating must be between 1 and 5'}), 400
                review.rating = data['rating']
            if 'title' in data: review.title = data['title'].strip()
            if 'content' in data: review.content = data['content'].strip()
            if 'visit_date' in data:
                if data['visit_date']:
                    try: review.visit_date = datetime.strptime(data['visit_date'], '%Y-%m-%d').date()
                    except ValueError: return jsonify({'success': False, 'error': 'Invalid date format'}), 400
                else: review.visit_date = None
            if 'package_used' in data: review.package_used = data['package_used'].strip()
            review.is_edited = True
            review.edited_at = datetime.now(UTC)
            review.edited_by_id = g.current_user.id
            review.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Review updated', 'data': review.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/reviews/<int:review_id>", methods=["DELETE"])
    @login_required
    def delete_review(review_id):
        try:
            review = CustomerReview.query.get(review_id)
            if not review:
                return jsonify({'success': False, 'error': 'Review not found'}), 404
            review.is_active = False
            review.updated_at = datetime.now(UTC)
            db.session.commit()
            if g.current_user.is_admin:
                log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='review', resource_id=review_id, ip_address=request.remote_addr)
                db.session.add(log)
                db.session.commit()
            return jsonify({'success': True, 'message': 'Review deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/reviews/<int:review_id>/replies", methods=["POST"])
    @login_required
    def create_reply(review_id):
        try:
            review = CustomerReview.query.get(review_id)
            if not review or not review.is_active:
                return jsonify({'success': False, 'error': 'Review not found'}), 404
            data = request.get_json()
            if not data.get('content'):
                return jsonify({'success': False, 'error': 'Reply content is required'}), 400
            reply = ReviewReply(review_id=review_id, user_id=g.current_user.id, content=data['content'])
            db.session.add(reply)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Reply added', 'data': reply.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/replies/<int:reply_id>", methods=["DELETE"])
    @login_required
    def delete_reply(reply_id):
        try:
            reply = ReviewReply.query.get(reply_id)
            if not reply:
                return jsonify({'success': False, 'error': 'Reply not found'}), 404
            if not g.current_user.is_admin and reply.user_id != g.current_user.id:
                return jsonify({'success': False, 'error': 'Permission denied'}), 403
            reply.is_active = False
            db.session.commit()
            return jsonify({'success': True, 'message': 'Reply deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/reviews/<int:review_id>/media", methods=["POST"])
    @admin_required
    def add_review_media(review_id):
        try:
            review = CustomerReview.query.get(review_id)
            if not review:
                return jsonify({'success': False, 'error': 'Review not found'}), 404
            files = request.files.getlist('media')
            if not files:
                return jsonify({'success': False, 'error': 'No files provided'}), 400
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            added = []
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'reviews', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = ReviewMedia(review_id=review_id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], uploaded_by_id=g.current_user.id)
                        db.session.add(media)
                        added.append(media)
            db.session.commit()
            return jsonify({'success': True, 'message': f'{len(added)} media files added', 'data': [m.to_dict() for m in added]}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/review-media/<int:media_id>", methods=["DELETE"])
    @admin_required
    def delete_review_media(media_id):
        try:
            media = ReviewMedia.query.get(media_id)
            if not media:
                return jsonify({'success': False, 'error': 'Media not found'}), 404
            delete_uploaded_file(media.file_url)
            db.session.delete(media)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Media deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= PACKAGE CATEGORIES =================
    @app.route("/api/package-categories", methods=["GET"])
    def get_package_categories():
        try:
            categories = PackageCategory.query.filter_by(is_active=True).order_by(PackageCategory.display_order).all()
            return jsonify({'success': True, 'data': [cat.to_dict() for cat in categories]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/package-categories", methods=["POST"])
    @permission_required('can_manage_packages')
    def create_package_category():
        try:
            data = request.get_json()
            if not data.get('name'):
                return jsonify({'success': False, 'error': 'Category name required'}), 400
            slug = data.get('slug') or data['name'].lower().replace(' ', '-')
            if PackageCategory.query.filter_by(slug=slug).first():
                return jsonify({'success': False, 'error': 'Category slug already exists'}), 400
            category = PackageCategory(name=data['name'], slug=slug, description=data.get('description', ''), display_order=data.get('display_order', 0))
            db.session.add(category)
            db.session.commit()
            return jsonify({'success': True, 'data': category.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= PACKAGES =================
    @app.route("/api/packages", methods=["GET"])
    def get_packages():
        try:
            category = request.args.get('category')
            featured = request.args.get('featured', type=bool)
            park_id = request.args.get('park_id', type=int)
            park_slug = request.args.get('park')

            query = Package.query.filter_by(is_active=True)
            if category:
                query = query.join(PackageCategory).filter(PackageCategory.slug == category)
            if featured is not None:
                query = query.filter_by(is_featured=featured)
            if park_id:
                query = query.filter(Package.park_id == park_id)
            elif park_slug:
                park = Park.query.filter_by(slug=park_slug, is_active=True).first()
                if park:
                    pkg_ids = [p.id for p in park.get_packages()]
                    if pkg_ids:
                        query = query.filter(Package.id.in_(pkg_ids))
                    else:
                        query = query.filter(db.false())
                else:
                    query = query.filter(db.false())

            packages = query.order_by(Package.display_order, Package.created_at.desc()).all()
            return jsonify({'success': True, 'count': len(packages), 'data': [pkg.to_dict() for pkg in packages]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/packages/<int:package_id>", methods=["GET"])
    def get_package(package_id):
        try:
            package = Package.query.get(package_id)
            if not package:
                return jsonify({'success': False, 'error': 'Package not found'}), 404
            return jsonify({'success': True, 'data': package.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    def _resolve_park_id(raw_value):
        if raw_value in (None, '', 'null', 'undefined'):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    @app.route("/api/packages", methods=["POST"])
    @permission_required('can_manage_packages')
    def create_package():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                for key in ['prices', 'includes', 'excludes']:
                    if key in data and isinstance(data[key], str):
                        try:
                            data[key] = json.loads(data[key])
                        except: pass
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            if not data.get('name'):
                return jsonify({'success': False, 'error': 'Package name required'}), 400
            slug = data.get('slug') or data['name'].lower().replace(' ', '-').replace('/', '-')
            base_slug = slug
            counter = 1
            while Package.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            prices = data.get('prices', {})
            if isinstance(prices, str):
                prices = json.loads(prices)
            package = Package(
                name=data['name'], slug=slug,
                description=data.get('description', ''),
                short_description=data.get('short_description', ''),
                total_days=int(data.get('total_days', 1)),
                total_nights=int(data.get('total_nights', 0)),
                full_itinerary=data.get('full_itinerary', ''),
                category_id=data.get('category_id'),
                park_id=_resolve_park_id(data.get('park_id')),
                prices={'pax_2': prices.get('pax_2'), 'pax_4': prices.get('pax_4'), 'pax_6': prices.get('pax_6'), 'pax_8': prices.get('pax_8'), 'single_supplement': prices.get('single_supplement'), 'child_price': prices.get('child_price')},
                price_notes=data.get('price_notes', ''),
                includes=data.get('includes', []) if isinstance(data.get('includes'), list) else [],
                excludes=data.get('excludes', []) if isinstance(data.get('excludes'), list) else [],
                is_featured=data.get('is_featured') in [True, 'true', 'True', '1'],
                display_order=int(data.get('display_order', 0)),
                created_by_id=g.current_user.id,
            )
            db.session.add(package)
            db.session.flush()
            days_data = data.get('itinerary_days')
            if days_data:
                if isinstance(days_data, str):
                    days_data = json.loads(days_data)
                for day in days_data:
                    day_obj = PackageDay(package_id=package.id, day_number=day.get('day_number', 1), title=day.get('title', f"Day {day.get('day_number', 1)}"), description=day.get('description', ''), activities=day.get('activities', []), meals=day.get('meals', []), accommodation=day.get('accommodation', ''), park_name=day.get('park_name', ''))
                    db.session.add(day_obj)
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            is_first = True
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'packages', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = PackageMedia(package_id=package.id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], is_main=is_first and media_type == 'image', source='upload')
                        db.session.add(media)
                        is_first = False
            db.session.commit()
            return jsonify({'success': True, 'message': 'Package created successfully', 'data': package.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/packages/<int:package_id>", methods=["PUT"])
    @permission_required('can_manage_packages')
    def update_package(package_id):
        try:
            package = Package.query.get(package_id)
            if not package:
                return jsonify({'success': False, 'error': 'Package not found'}), 404
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                for key in ['prices', 'includes', 'excludes', 'itinerary_days']:
                    if key in data and isinstance(data[key], str):
                        try:
                            data[key] = json.loads(data[key])
                        except: pass
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            if 'name' in data: package.name = data['name']
            if 'description' in data: package.description = data['description']
            if 'short_description' in data: package.short_description = data['short_description']
            if 'total_days' in data: package.total_days = int(data['total_days'])
            if 'total_nights' in data: package.total_nights = int(data['total_nights'])
            if 'full_itinerary' in data: package.full_itinerary = data['full_itinerary']
            if 'category_id' in data: package.category_id = data['category_id']
            if 'park_id' in data: package.park_id = _resolve_park_id(data['park_id'])
            if 'prices' in data:
                prices = data['prices']
                package.prices = {'pax_2': prices.get('pax_2'), 'pax_4': prices.get('pax_4'), 'pax_6': prices.get('pax_6'), 'pax_8': prices.get('pax_8'), 'single_supplement': prices.get('single_supplement'), 'child_price': prices.get('child_price')}
            if 'price_notes' in data: package.price_notes = data['price_notes']
            if 'includes' in data and isinstance(data['includes'], list): package.includes = data['includes']
            if 'excludes' in data and isinstance(data['excludes'], list): package.excludes = data['excludes']
            if 'is_featured' in data: package.is_featured = data['is_featured'] in [True, 'true', 'True', '1']
            if 'is_active' in data: package.is_active = data['is_active'] in [True, 'true', 'True', '1']
            if 'display_order' in data: package.display_order = int(data['display_order'])
            if 'itinerary_days' in data:
                PackageDay.query.filter_by(package_id=package_id).delete()
                for day in data['itinerary_days']:
                    day_obj = PackageDay(package_id=package.id, day_number=day.get('day_number', 1), title=day.get('title', f"Day {day.get('day_number', 1)}"), description=day.get('description', ''), activities=day.get('activities', []), meals=day.get('meals', []), accommodation=day.get('accommodation', ''), park_name=day.get('park_name', ''))
                    db.session.add(day_obj)
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'packages', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = PackageMedia(package_id=package.id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], source='upload')
                        db.session.add(media)
            package.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Package updated', 'data': package.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/packages/<int:package_id>", methods=["DELETE"])
    @permission_required('can_manage_packages')
    def delete_package(package_id):
        try:
            package = Package.query.get(package_id)
            if not package:
                return jsonify({'success': False, 'error': 'Package not found'}), 404
            package.is_active = False
            package.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='package', resource_id=package_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Package deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/packages/<int:package_id>/media", methods=["POST"])
    @permission_required('can_manage_packages')
    def add_package_media(package_id):
        try:
            package = Package.query.get(package_id)
            if not package:
                return jsonify({'success': False, 'error': 'Package not found'}), 404
            if request.is_json:
                data = request.get_json()
                url = data.get('url')
                if not url:
                    return jsonify({'success': False, 'error': 'URL required'}), 400
                media_type = 'video' if any(ext in url.lower() for ext in ['.mp4', '.avi', '.mov', '.webm']) else 'image'
                media = PackageMedia(package_id=package_id, media_type=media_type, file_url=url, is_main=data.get('is_main', False), source='url')
                db.session.add(media)
                db.session.commit()
                return jsonify({'success': True, 'data': media.to_dict()}), 201
            files = request.files.getlist('media')
            if not files:
                return jsonify({'success': False, 'error': 'No files provided'}), 400
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            added = []
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'packages', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = PackageMedia(package_id=package_id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], source='upload')
                        db.session.add(media)
                        added.append(media)
            db.session.commit()
            return jsonify({'success': True, 'message': f'{len(added)} media files added', 'data': [m.to_dict() for m in added]}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/package-media/<int:media_id>", methods=["DELETE"])
    @permission_required('can_manage_packages')
    def delete_package_media(media_id):
        try:
            media = PackageMedia.query.get(media_id)
            if not media:
                return jsonify({'success': False, 'error': 'Media not found'}), 404
            if media.source == 'upload':
                delete_uploaded_file(media.file_url)
            db.session.delete(media)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Media deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/package-media/<int:media_id>/set-main", methods=["POST"])
    @permission_required('can_manage_packages')
    def set_package_main_media(media_id):
        try:
            media = PackageMedia.query.get(media_id)
            if not media:
                return jsonify({'success': False, 'error': 'Media not found'}), 404
            if media.media_type != 'image':
                return jsonify({'success': False, 'error': 'Only images can be set as main'}), 400
            PackageMedia.query.filter_by(package_id=media.package_id).update({'is_main': False})
            media.is_main = True
            db.session.commit()
            return jsonify({'success': True, 'message': 'Main image set'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= EXCURSIONS =================
    @app.route("/api/excursions", methods=["GET"])
    def get_excursions():
        try:
            featured = request.args.get('featured', type=bool)
            query = Excursion.query.filter_by(is_active=True)
            if featured is not None: query = query.filter_by(is_featured=featured)
            excursions = query.order_by(Excursion.display_order, Excursion.created_at.desc()).all()
            return jsonify({'success': True, 'count': len(excursions), 'data': [exc.to_dict() for exc in excursions]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions/<int:excursion_id>", methods=["GET"])
    def get_excursion(excursion_id):
        try:
            excursion = Excursion.query.get(excursion_id)
            if not excursion:
                return jsonify({'success': False, 'error': 'Excursion not found'}), 404
            return jsonify({'success': True, 'data': excursion.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions", methods=["POST"])
    @permission_required('can_manage_excursions')
    def create_excursion():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                for key in ['includes', 'excludes']:
                    if key in data and isinstance(data[key], str):
                        try:
                            data[key] = json.loads(data[key])
                        except: pass
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            if not data.get('name'):
                return jsonify({'success': False, 'error': 'Excursion name required'}), 400
            if not data.get('price'):
                return jsonify({'success': False, 'error': 'Price required'}), 400
            slug = data.get('slug') or data['name'].lower().replace(' ', '-').replace('/', '-')
            base_slug = slug
            counter = 1
            while Excursion.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            excursion = Excursion(name=data['name'], slug=slug, description=data.get('description', ''), short_description=data.get('short_description', ''), price=float(data['price']), price_currency=data.get('price_currency', 'USD'), price_notes=data.get('price_notes', ''), duration_hours=float(data['duration_hours']) if data.get('duration_hours') else None, duration_text=data.get('duration_text', ''), location=data.get('location', ''), includes=data.get('includes', []) if isinstance(data.get('includes'), list) else [], excludes=data.get('excludes', []) if isinstance(data.get('excludes'), list) else [], is_featured=data.get('is_featured') in [True, 'true', 'True', '1'], display_order=int(data.get('display_order', 0)), created_by_id=g.current_user.id)
            db.session.add(excursion)
            db.session.flush()
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            files = files[:MAX_EXCURSION_MEDIA]
            is_first = True
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'excursions', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = ExcursionMedia(excursion_id=excursion.id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], is_main=is_first and media_type == 'image', source='upload')
                        db.session.add(media)
                        is_first = False
            db.session.commit()
            return jsonify({'success': True, 'message': 'Excursion created successfully', 'data': excursion.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions/<int:excursion_id>", methods=["PUT"])
    @permission_required('can_manage_excursions')
    def update_excursion(excursion_id):
        try:
            excursion = Excursion.query.get(excursion_id)
            if not excursion:
                return jsonify({'success': False, 'error': 'Excursion not found'}), 404
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                for key in ['includes', 'excludes']:
                    if key in data and isinstance(data[key], str):
                        try:
                            data[key] = json.loads(data[key])
                        except: pass
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            if 'name' in data: excursion.name = data['name']
            if 'description' in data: excursion.description = data['description']
            if 'short_description' in data: excursion.short_description = data['short_description']
            if 'price' in data: excursion.price = float(data['price'])
            if 'price_currency' in data: excursion.price_currency = data['price_currency']
            if 'price_notes' in data: excursion.price_notes = data['price_notes']
            if 'duration_hours' in data: excursion.duration_hours = float(data['duration_hours']) if data['duration_hours'] else None
            if 'duration_text' in data: excursion.duration_text = data['duration_text']
            if 'location' in data: excursion.location = data['location']
            if 'includes' in data and isinstance(data['includes'], list): excursion.includes = data['includes']
            if 'excludes' in data and isinstance(data['excludes'], list): excursion.excludes = data['excludes']
            if 'is_featured' in data: excursion.is_featured = data['is_featured'] in [True, 'true', 'True', '1']
            if 'is_active' in data: excursion.is_active = data['is_active'] in [True, 'true', 'True', '1']
            if 'display_order' in data: excursion.display_order = int(data['display_order'])
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            existing_count = ExcursionMedia.query.filter_by(excursion_id=excursion.id).count()
            remaining_slots = max(0, MAX_EXCURSION_MEDIA - existing_count)
            files = files[:remaining_slots]
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'excursions', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = ExcursionMedia(excursion_id=excursion.id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], source='upload')
                        db.session.add(media)
            excursion.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Excursion updated', 'data': excursion.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions/<int:excursion_id>", methods=["DELETE"])
    @permission_required('can_manage_excursions')
    def delete_excursion(excursion_id):
        try:
            excursion = Excursion.query.get(excursion_id)
            if not excursion:
                return jsonify({'success': False, 'error': 'Excursion not found'}), 404
            excursion.is_active = False
            excursion.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='excursion', resource_id=excursion_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Excursion deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions/<int:excursion_id>/media", methods=["POST"])
    @permission_required('can_manage_excursions')
    def add_excursion_media(excursion_id):
        try:
            excursion = Excursion.query.get(excursion_id)
            if not excursion:
                return jsonify({'success': False, 'error': 'Excursion not found'}), 404
            existing_count = ExcursionMedia.query.filter_by(excursion_id=excursion_id).count()
            if existing_count >= MAX_EXCURSION_MEDIA:
                return jsonify({'success': False, 'error': f'This excursion already has the maximum of {MAX_EXCURSION_MEDIA} media files'}), 400
            if request.is_json:
                data = request.get_json()
                url = data.get('url')
                if not url:
                    return jsonify({'success': False, 'error': 'URL required'}), 400
                media_type = 'video' if any(ext in url.lower() for ext in ['.mp4', '.avi', '.mov', '.webm']) else 'image'
                media = ExcursionMedia(excursion_id=excursion_id, media_type=media_type, file_url=url, is_main=data.get('is_main', False), source='url')
                db.session.add(media)
                db.session.commit()
                return jsonify({'success': True, 'data': media.to_dict()}), 201
            files = request.files.getlist('media')
            if not files:
                return jsonify({'success': False, 'error': 'No files provided'}), 400
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS'] | app.config['ALLOWED_VIDEO_EXTENSIONS']
            remaining_slots = MAX_EXCURSION_MEDIA - existing_count
            files = files[:remaining_slots]
            added = []
            for file in files:
                if file and file.filename:
                    file_url, metadata = save_uploaded_file(file, 'excursions', allowed_exts)
                    if file_url:
                        media_type = get_media_type(file.filename)
                        media = ExcursionMedia(excursion_id=excursion_id, media_type=media_type or 'image', file_url=file_url, file_name=metadata['file_name'], file_size=metadata['file_size'], mime_type=metadata['mime_type'], source='upload')
                        db.session.add(media)
                        added.append(media)
            db.session.commit()
            return jsonify({'success': True, 'message': f'{len(added)} media files added', 'data': [m.to_dict() for m in added]}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursion-media/<int:media_id>", methods=["DELETE"])
    @permission_required('can_manage_excursions')
    def delete_excursion_media(media_id):
        try:
            media = ExcursionMedia.query.get(media_id)
            if not media:
                return jsonify({'success': False, 'error': 'Media not found'}), 404
            if media.source == 'upload':
                delete_uploaded_file(media.file_url)
            db.session.delete(media)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Media deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursion-media/<int:media_id>/set-main", methods=["POST"])
    @permission_required('can_manage_excursions')
    def set_excursion_main_media(media_id):
        try:
            media = ExcursionMedia.query.get(media_id)
            if not media:
                return jsonify({'success': False, 'error': 'Media not found'}), 404
            if media.media_type != 'image':
                return jsonify({'success': False, 'error': 'Only images can be set as main'}), 400
            ExcursionMedia.query.filter_by(excursion_id=media.excursion_id).update({'is_main': False})
            media.is_main = True
            db.session.commit()
            return jsonify({'success': True, 'message': 'Main image set'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/excursions/slug/<slug>", methods=["GET"])
    def get_excursion_by_slug(slug):
        try:
            exc = Excursion.query.filter_by(slug=slug, is_active=True).first()
            if not exc:
                return jsonify({'success': False, 'error': 'Excursion not found'}), 404
            return jsonify({'success': True, 'data': exc.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= TRANSFERS =================
    @app.route("/api/transfers", methods=["GET"])
    def get_transfers():
        try:
            transfers = Transfer.query.filter_by(is_active=True).order_by(Transfer.display_order, Transfer.created_at.desc()).all()
            return jsonify({'success': True, 'count': len(transfers), 'data': [t.to_dict() for t in transfers]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/transfers/<int:transfer_id>", methods=["GET"])
    def get_transfer(transfer_id):
        try:
            t = Transfer.query.get(transfer_id)
            if not t or not t.is_active:
                return jsonify({'success': False, 'error': 'Transfer not found'}), 404
            return jsonify({'success': True, 'data': t.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/transfers", methods=["POST"])
    @permission_required('can_manage_excursions')
    def create_transfer():
        try:
            data = request.get_json() or {}
            if not data.get('route'):
                return jsonify({'success': False, 'error': 'Route name is required'}), 400
            slug = data.get('slug') or data['route'].lower().replace(' ', '-').replace('/', '-').replace('–', '-')
            base_slug = slug
            counter = 1
            while Transfer.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            def _num(v):
                if v is None or v == '': return None
                try: return float(v)
                except (TypeError, ValueError): return None
            t = Transfer(route=data['route'].strip(), slug=slug, description=data.get('description', '').strip(), currency=data.get('currency', 'EUR'), price_2_pax=_num(data.get('price_2_pax')), price_4_pax=_num(data.get('price_4_pax')), price_6_pax=_num(data.get('price_6_pax')), per_car=bool(data.get('per_car', False)), is_active=data.get('is_active', True), display_order=int(data.get('display_order', 0) or 0), created_by_id=g.current_user.id)
            db.session.add(t)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Transfer created successfully', 'data': t.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/transfers/<int:transfer_id>", methods=["PUT"])
    @permission_required('can_manage_excursions')
    def update_transfer(transfer_id):
        try:
            t = Transfer.query.get(transfer_id)
            if not t:
                return jsonify({'success': False, 'error': 'Transfer not found'}), 404
            data = request.get_json() or {}
            def _num(v):
                if v is None or v == '': return None
                try: return float(v)
                except (TypeError, ValueError): return None
            if 'route' in data: t.route = data['route'].strip()
            if 'description' in data: t.description = data['description'].strip()
            if 'currency' in data: t.currency = data['currency']
            if 'price_2_pax' in data: t.price_2_pax = _num(data['price_2_pax'])
            if 'price_4_pax' in data: t.price_4_pax = _num(data['price_4_pax'])
            if 'price_6_pax' in data: t.price_6_pax = _num(data['price_6_pax'])
            if 'per_car' in data: t.per_car = bool(data['per_car'])
            if 'is_active' in data: t.is_active = bool(data['is_active'])
            if 'display_order' in data: t.display_order = int(data['display_order'] or 0)
            t.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Transfer updated', 'data': t.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/transfers/<int:transfer_id>", methods=["DELETE"])
    @permission_required('can_manage_excursions')
    def delete_transfer(transfer_id):
        try:
            t = Transfer.query.get(transfer_id)
            if not t:
                return jsonify({'success': False, 'error': 'Transfer not found'}), 404
            t.is_active = False
            t.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='transfer', resource_id=transfer_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Transfer deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= BLOG =================
    @app.route("/api/blog", methods=["GET"])
    def get_blog_posts():
        try:
            category = request.args.get('category')
            featured = request.args.get('featured', type=bool)
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            query = BlogPost.query.filter_by(is_active=True, is_published=True)
            if category and category.lower() != 'all posts': query = query.filter_by(category=category)
            if featured is not None: query = query.filter_by(is_featured=featured)
            pagination = query.order_by(BlogPost.display_order, BlogPost.published_at.desc(), BlogPost.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            return jsonify({'success': True, 'count': pagination.total, 'data': [p.to_dict(include_content=False) for p in pagination.items], 'pagination': {'page': page, 'per_page': per_page, 'total': pagination.total, 'pages': pagination.pages}}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog/<int:post_id>", methods=["GET"])
    def get_blog_post(post_id):
        try:
            post = BlogPost.query.get(post_id)
            if not post or not post.is_active:
                return jsonify({'success': False, 'error': 'Post not found'}), 404
            try:
                post.views = (post.views or 0) + 1
                db.session.commit()
            except Exception: db.session.rollback()
            return jsonify({'success': True, 'data': post.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog/slug/<slug>", methods=["GET"])
    def get_blog_post_by_slug(slug):
        try:
            post = BlogPost.query.filter_by(slug=slug, is_active=True).first()
            if not post:
                return jsonify({'success': False, 'error': 'Post not found'}), 404
            try:
                post.views = (post.views or 0) + 1
                db.session.commit()
            except Exception: db.session.rollback()
            return jsonify({'success': True, 'data': post.to_dict()}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog/categories", methods=["GET"])
    def get_blog_categories():
        try:
            rows = db.session.query(BlogPost.category).filter(BlogPost.is_active == True, BlogPost.is_published == True).distinct().all()
            cats = [r[0] for r in rows if r[0]]
            return jsonify({'success': True, 'data': cats}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog", methods=["POST"])
    @permission_required('can_manage_blog')
    def create_blog_post():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                cover_file = request.files.get('cover_image')
            else:
                data = request.get_json() or {}
                cover_file = None
            if not data.get('title'):
                return jsonify({'success': False, 'error': 'Title is required'}), 400
            slug = data.get('slug') or re.sub(r'[^a-z0-9]+', '-', data['title'].lower()).strip('-')
            base_slug = slug
            counter = 1
            while BlogPost.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            tags = data.get('tags', [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = [t.strip() for t in tags.split(',') if t.strip()]
            if data.get('published_at'):
                try: published_at = datetime.fromisoformat(data['published_at'].replace('Z', ''))
                except Exception: published_at = datetime.now(UTC)
            else: published_at = datetime.now(UTC)
            cover_url = data.get('cover_image', '') or ''
            if cover_file and cover_file.filename:
                allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
                url, _ = save_uploaded_file(cover_file, 'blog', allowed_exts)
                if url: cover_url = url
            post = BlogPost(title=data['title'].strip(), slug=slug, excerpt=data.get('excerpt', '').strip(), content=data.get('content', ''), category=data.get('category', 'Travel Tips').strip() or 'Travel Tips', author_name=data.get('author_name', '').strip() or g.current_user.name, author_avatar=data.get('author_avatar', '').strip(), cover_image=cover_url, cover_image_alt=data.get('cover_image_alt', '').strip(), tags=tags, is_featured=data.get('is_featured') in [True, 'true', 'True', '1'], is_published=data.get('is_published') not in [False, 'false', 'False', '0'], is_active=data.get('is_active') not in [False, 'false', 'False', '0'], display_order=int(data.get('display_order', 0) or 0), published_at=published_at, author_id=g.current_user.id)
            db.session.add(post)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Blog post created', 'data': post.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog/<int:post_id>", methods=["PUT"])
    @permission_required('can_manage_blog')
    def update_blog_post(post_id):
        try:
            post = BlogPost.query.get(post_id)
            if not post:
                return jsonify({'success': False, 'error': 'Post not found'}), 404
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                cover_file = request.files.get('cover_image')
            else:
                data = request.get_json() or {}
                cover_file = None
            if 'title' in data: post.title = data['title'].strip()
            if 'excerpt' in data: post.excerpt = data['excerpt'].strip()
            if 'content' in data: post.content = data['content']
            if 'category' in data: post.category = data['category'].strip() or 'Travel Tips'
            if 'author_name' in data: post.author_name = data['author_name'].strip()
            if 'author_avatar' in data: post.author_avatar = data['author_avatar'].strip()
            if 'cover_image' in data and data['cover_image']: post.cover_image = data['cover_image'].strip()
            if 'cover_image_alt' in data: post.cover_image_alt = data['cover_image_alt'].strip()
            if 'tags' in data:
                tags = data['tags']
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except Exception:
                        tags = [t.strip() for t in tags.split(',') if t.strip()]
                post.tags = tags
            if 'is_featured' in data: post.is_featured = data['is_featured'] in [True, 'true', 'True', '1']
            if 'is_published' in data: post.is_published = data['is_published'] not in [False, 'false', 'False', '0']
            if 'is_active' in data: post.is_active = data['is_active'] not in [False, 'false', 'False', '0']
            if 'display_order' in data: post.display_order = int(data['display_order'] or 0)
            if cover_file and cover_file.filename:
                allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
                url, _ = save_uploaded_file(cover_file, 'blog', allowed_exts)
                if url: post.cover_image = url
            post.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Blog post updated', 'data': post.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/blog/<int:post_id>", methods=["DELETE"])
    @permission_required('can_manage_blog')
    def delete_blog_post(post_id):
        try:
            post = BlogPost.query.get(post_id)
            if not post:
                return jsonify({'success': False, 'error': 'Post not found'}), 404
            post.is_active = False
            post.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='blog_post', resource_id=post_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Blog post deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/community-stories", methods=["POST"])
    @optional_auth
    def create_community_story():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                files = request.files.getlist('media')
            else:
                data = request.get_json() or {}
                files = []
            title = (data.get('title') or '').strip()
            content = (data.get('content') or '').strip()
            author_name = (data.get('author_name') or '').strip()
            category = (data.get('category') or 'Safari Review').strip()
            try: rating = int(data.get('rating') or 0)
            except (TypeError, ValueError): rating = 0
            if not title: return jsonify({'success': False, 'error': 'Title is required'}), 400
            if not content or len(content) < 20: return jsonify({'success': False, 'error': 'Content must be at least 20 characters'}), 400
            if not author_name: return jsonify({'success': False, 'error': 'Your name is required'}), 400
            if rating < 0 or rating > 5: return jsonify({'success': False, 'error': 'Rating must be between 0 and 5'}), 400
            base_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f"story-{uuid.uuid4().hex[:8]}"
            slug = base_slug
            counter = 1
            while BlogPost.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1
            community_token = uuid.uuid4().hex
            allowed_exts = app.config.get('ALLOWED_IMAGE_EXTENSIONS', set()) | app.config.get('ALLOWED_VIDEO_EXTENSIONS', set())
            saved_media_urls = []
            files = files[:4]
            for file in files:
                if file and file.filename:
                    url, _ = save_uploaded_file(file, 'blog', allowed_exts)
                    if url: saved_media_urls.append(url)
            cover_url = saved_media_urls[0] if saved_media_urls else ''
            tags = [category]
            if rating: tags.append(f'rating:{rating}')
            for url in saved_media_urls[1:]: tags.append(f'media:{url}')
            post = BlogPost(title=title, slug=slug, excerpt=content[:200] + ('…' if len(content) > 200 else ''), content=content, category=category, author_name=author_name, author_avatar='', cover_image=cover_url, cover_image_alt=title, tags=tags, is_featured=False, is_published=True, is_active=True, display_order=0, published_at=datetime.now(UTC), author_id=g.current_user.id if g.current_user else None, is_community=True, community_token=community_token)
            db.session.add(post)
            db.session.commit()
            app.logger.info(f"Community story submitted: '{title}' by {author_name} (media: {len(saved_media_urls)} file(s))")
            return jsonify({'success': True, 'message': 'Story submitted successfully', 'data': post.to_dict(), 'community_token': community_token}), 201
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Community story submission error: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= ABOUT =================
    @app.route("/api/about-images", methods=["GET"])
    def get_about_images():
        try:
            images = AboutImage.query.all()
            data = {img.section_key: img.to_dict() for img in images}
            return jsonify({'success': True, 'data': data}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-images", methods=["GET"])
    @admin_required
    def get_admin_about_images():
        try:
            images = AboutImage.query.order_by(AboutImage.section_key).all()
            return jsonify({'success': True, 'data': [img.to_dict() for img in images]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-images/<string:section_key>", methods=["POST"])
    @admin_required
    def update_about_image(section_key):
        try:
            if 'image' not in request.files:
                return jsonify({'success': False, 'error': 'No image file provided'}), 400
            file = request.files['image']
            if not file or not file.filename:
                return jsonify({'success': False, 'error': 'Invalid file'}), 400
            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
            file_url, metadata = save_uploaded_file(file, 'about', allowed_exts)
            if not file_url:
                return jsonify({'success': False, 'error': 'Invalid image format'}), 400
            img_record = AboutImage.query.filter_by(section_key=section_key).first()
            if img_record:
                img_record.image_url = file_url
                img_record.alt_text = request.form.get('alt_text', img_record.alt_text)
            else:
                img_record = AboutImage(section_key=section_key, image_url=file_url, alt_text=request.form.get('alt_text', ''))
                db.session.add(img_record)
            db.session.commit()
            return jsonify({'success': True, 'message': f'Image for {section_key} updated successfully', 'data': img_record.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/about-people", methods=["GET"])
    def get_about_people():
        try:
            people = AboutPerson.query.filter_by(is_active=True).order_by(AboutPerson.person_type, AboutPerson.display_order).all()
            founders = [p.to_dict() for p in people if p.person_type == 'founder']
            team = [p.to_dict() for p in people if p.person_type == 'team']
            return jsonify({'success': True, 'founders': founders, 'team': team}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-people", methods=["GET"])
    @admin_required
    def get_admin_about_people():
        try:
            people = AboutPerson.query.order_by(AboutPerson.person_type, AboutPerson.display_order).all()
            return jsonify({'success': True, 'data': [p.to_dict() for p in people]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-people", methods=["POST"])
    @admin_required
    def create_about_person():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                image_file = request.files.get('image')
            else:
                data = request.get_json() or {}
                image_file = None
            if not data.get('name') or not data.get('role') or not data.get('person_type'):
                return jsonify({'success': False, 'error': 'Name, role, and person_type are required'}), 400
            if data['person_type'] not in ('founder', 'team'):
                return jsonify({'success': False, 'error': 'person_type must be "founder" or "team"'}), 400
            image_url = data.get('image_url', '')
            if image_file and image_file.filename:
                allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
                url, _ = save_uploaded_file(image_file, 'about', allowed_exts)
                if url: image_url = url
            person = AboutPerson(name=data['name'].strip(), role=data['role'].strip(), person_type=data['person_type'], image_url=image_url, display_order=int(data.get('display_order', 0) or 0), is_active=data.get('is_active', True) in [True, 'true', 'True', '1'])
            db.session.add(person)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Person added successfully', 'data': person.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-people/<int:person_id>", methods=["PUT"])
    @admin_required
    def update_about_person(person_id):
        try:
            person = AboutPerson.query.get(person_id)
            if not person:
                return jsonify({'success': False, 'error': 'Person not found'}), 404
            if request.content_type and 'multipart/form-data' in request.content_type:
                data = request.form.to_dict()
                image_file = request.files.get('image')
            else:
                data = request.get_json() or {}
                image_file = None
            if 'name' in data: person.name = data['name'].strip()
            if 'role' in data: person.role = data['role'].strip()
            if 'person_type' in data and data['person_type'] in ('founder', 'team'): person.person_type = data['person_type']
            if 'image_url' in data and data['image_url']: person.image_url = data['image_url'].strip()
            if 'display_order' in data: person.display_order = int(data['display_order'] or 0)
            if 'is_active' in data: person.is_active = data['is_active'] in [True, 'true', 'True', '1']
            if image_file and image_file.filename:
                allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
                url, _ = save_uploaded_file(image_file, 'about', allowed_exts)
                if url: person.image_url = url
            person.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Person updated', 'data': person.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/about-people/<int:person_id>", methods=["DELETE"])
    @admin_required
    def delete_about_person(person_id):
        try:
            person = AboutPerson.query.get(person_id)
            if not person:
                return jsonify({'success': False, 'error': 'Person not found'}), 404
            delete_uploaded_file(person.image_url)
            db.session.delete(person)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Person deleted'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= PARKS (NEW) =================
    @app.route("/api/parks", methods=["GET"])
    def get_parks():
        try:
            parks = Park.query.filter_by(is_active=True)\
                              .order_by(Park.display_order, Park.name).all()
            return jsonify({
                'success': True,
                'count': len(parks),
                'data': [p.to_dict() for p in parks]
            }), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/parks/slug/<slug>", methods=["GET"])
    def get_park_by_slug(slug):
        try:
            park = Park.query.filter_by(slug=slug, is_active=True).first()
            if not park:
                return jsonify({'success': False, 'error': 'Park not found'}), 404
            return jsonify({'success': True, 'data': park.to_dict(include_packages=True)}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/parks", methods=["GET"])
    @admin_required
    def admin_get_parks():
        try:
            parks = Park.query.order_by(Park.display_order, Park.name).all()
            return jsonify({'success': True, 'data': [p.to_dict() for p in parks]}), 200
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/parks", methods=["POST"])
    @permission_required('can_manage_parks')
    def create_park():
        try:
            payload, hero_file, gallery_files, incoming_gallery = _parse_park_payload()
            if not payload['name']:
                return jsonify({'success': False, 'error': 'Name is required'}), 400

            base_slug = _slugify(payload['name'])
            slug = _unique_park_slug(base_slug)

            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']

            hero_url = ''
            if hero_file and hero_file.filename:
                url, _ = save_uploaded_file(hero_file, 'parks', allowed_exts)
                if url: hero_url = url

            gallery_urls = list(incoming_gallery or [])
            for f in gallery_files:
                if f and f.filename:
                    url, _ = save_uploaded_file(f, 'parks', allowed_exts)
                    if url: gallery_urls.append(url)

            park = Park(
                slug=slug,
                hero_image=hero_url,
                gallery=gallery_urls,
                created_by_id=g.current_user.id,
                **payload,
            )
            db.session.add(park)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Park created successfully', 'data': park.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/parks/<int:park_id>", methods=["PUT"])
    @permission_required('can_manage_parks')
    def update_park(park_id):
        try:
            park = Park.query.get(park_id)
            if not park:
                return jsonify({'success': False, 'error': 'Park not found'}), 404

            payload, hero_file, gallery_files, incoming_gallery = _parse_park_payload()

            # If name changed, regenerate slug to keep URL stable-ish but unique
            if payload['name'] and payload['name'] != park.name:
                base_slug = _slugify(payload['name'])
                park.slug = _unique_park_slug(base_slug, ignore_id=park.id)

            for k, v in payload.items():
                setattr(park, k, v)

            allowed_exts = app.config['ALLOWED_IMAGE_EXTENSIONS']
            if hero_file and hero_file.filename:
                url, _ = save_uploaded_file(hero_file, 'parks', allowed_exts)
                if url: park.hero_image = url

            # Merge gallery: incoming list + newly uploaded
            gallery_urls = list(incoming_gallery or park.gallery or [])
            for f in gallery_files:
                if f and f.filename:
                    url, _ = save_uploaded_file(f, 'parks', allowed_exts)
                    if url: gallery_urls.append(url)
            park.gallery = gallery_urls

            park.updated_at = datetime.now(UTC)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Park updated', 'data': park.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route("/api/admin/parks/<int:park_id>", methods=["DELETE"])
    @permission_required('can_manage_parks')
    def delete_park(park_id):
        try:
            park = Park.query.get(park_id)
            if not park:
                return jsonify({'success': False, 'error': 'Park not found'}), 404
            park.is_active = False
            park.updated_at = datetime.now(UTC)
            db.session.commit()
            log = AdminActionLog(admin_id=g.current_user.id, action_type='delete', resource_type='park', resource_id=park_id, ip_address=request.remote_addr)
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Park deactivated'}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': str(e)}), 500

    # ================= CONTACT / INQUIRIES =================
    @app.route("/api/contact", methods=["POST"])
    @optional_auth
    def contact_form():
        try:
            data = request.get_json() or {}
            for field in ("name", "email", "message"):
                if not data.get(field):
                    return jsonify({'success': False, 'error': f'{field} is required'}), 400
            email_pattern = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
            if not email_pattern.match(str(data['email'])):
                return jsonify({'success': False, 'error': 'Invalid email format'}), 400
            if len(str(data['message']).strip()) < 5:
                return jsonify({'success': False, 'error': 'Message is too short'}), 400
            name = data['name'].strip()
            email = data['email'].strip().lower()
            phone = (data.get('phone') or '').strip()
            subject = (data.get('subject') or 'General Inquiry').strip()
            message = data['message'].strip()

            admin_recipient = app.config.get('MAIL_ADMIN_RECIPIENT', 'info@joztembotours.com')
            sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
            ticket_id = f"JT-{uuid.uuid4().hex[:8].upper()}"

            admin_html = f"""
            <h3>New Contact Form Submission</h3>
            <p><strong>Name:</strong> {name}</p>
            <p><strong>Email:</strong> {email}</p>
            <p><strong>Phone:</strong> {phone or '—'}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <div class="highlight-box">
                <p><strong>Message:</strong></p>
                <p>{message}</p>
            </div>
            """
            admin_msg = Message(subject=f"[{ticket_id}] {subject} — from {name}", recipients=[admin_recipient])
            if sender: admin_msg.sender = sender
            if email: admin_msg.reply_to = email
            set_email_html(admin_msg, f"Contact Form: {ticket_id}", admin_html)

            customer_html = f"""
            <h3>Hi {name},</h3>
            <p>We have received your message and our team will get back to you within 24 hours.</p>
            <p>Here is your reference number for your records:</p>
            <div class="highlight-box" style="text-align: center; font-size: 18px;">
                <strong>{ticket_id}</strong>
            </div>
            <p>Your message:</p>
            <p style="color: #666; font-style: italic;">"{message}"</p>
            """
            customer_msg = Message(subject=f"We received your message — {ticket_id}", recipients=[email])
            if sender: customer_msg.sender = sender
            set_email_html(customer_msg, "Message Received", customer_html)

            with mail.connect() as conn:
                conn.send(admin_msg)
                conn.send(customer_msg)

            app.logger.info(f"Contact form submitted: {ticket_id} from {email} ({subject})")
            return jsonify({'success': True, 'message': 'Message sent successfully', 'ticket_id': ticket_id}), 201
        except Exception as e:
            app.logger.error(f"Contact form error: {e}")
            return jsonify({'success': False, 'error': f'Failed to send message: {str(e)}'}), 500

    @app.route("/api/transfer-quick-inquiry", methods=["POST"])
    def create_quick_transfer_inquiry():
        try:
            data = request.get_json(silent=True) or {}

            required_fields = (
                "name", "email", "phone", "pickup_location",
                "dropoff_location", "date_time", "transfer_type"
            )
            missing = [field for field in required_fields if not str(data.get(field, "")).strip()]
            if missing:
                return jsonify({
                    "success": False,
                    "error": f"Please complete all required fields: {', '.join(missing)}"
                }), 400

            email = str(data["email"]).strip().lower()
            email_pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
            if not email_pattern.match(email):
                return jsonify({"success": False, "error": "Please enter a valid email address."}), 400

            try:
                pax = int(data.get("passengers", 1))
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "Passenger count must be a number."}), 400

            if pax < 1 or pax > 20:
                return jsonify({"success": False, "error": "Passenger count must be between 1 and 20."}), 400

            transfer_type = str(data["transfer_type"]).strip()
            if transfer_type not in ("airport-to-hotel", "hotel-to-airport"):
                return jsonify({"success": False, "error": "Please select a valid transfer type."}), 400

            reference_code = f"JT-TRF-{uuid.uuid4().hex[:6].upper()}"
            name = escape(str(data["name"]).strip())
            phone = escape(str(data["phone"]).strip())
            pickup = escape(str(data["pickup_location"]).strip())
            dropoff = escape(str(data["dropoff_location"]).strip())
            date_time = escape(str(data["date_time"]).strip())
            transfer_label = "Airport to Hotel" if transfer_type == "airport-to-hotel" else "Hotel to Airport"
            admin_recipient = app.config.get("MAIL_ADMIN_RECIPIENT", "info@joztembotours.com")
            sender = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")

            details_html = f"""
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                       style="border-collapse:collapse;font-size:14px;">
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Reference</td>
                        <td style="padding:10px 0;text-align:right;font-weight:bold;border-bottom:1px solid #eee;">{reference_code}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Transfer type</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{transfer_label}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Pickup location</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{pickup}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Drop-off location</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{dropoff}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Date &amp; time</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{date_time}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Passengers</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{pax}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;border-bottom:1px solid #eee;">Customer name</td>
                        <td style="padding:10px 0;text-align:right;border-bottom:1px solid #eee;">{name}</td></tr>
                    <tr><td style="padding:10px 0;color:#777;">Phone</td>
                        <td style="padding:10px 0;text-align:right;">{phone}</td></tr>
                </table>
            """

            admin_html = f"""
                <p style="margin-top:0;">A new transfer request has been submitted through the website.</p>
                <div class="highlight-box">
                    <strong>New transfer inquiry · {reference_code}</strong>
                </div>
                {details_html}
                <p style="margin-top:22px;">Reply directly to this email to contact the customer at {escape(email)}.</p>
            """
            admin_message = Message(
                subject=f"[{reference_code}] New transfer request — {name}",
                recipients=[admin_recipient]
            )
            if sender:
                admin_message.sender = sender
            admin_message.reply_to = email
            set_email_html(admin_message, "New Transfer Request", admin_html)
            customer_html = f"""
                <p>Hello {name},</p>
                <p>Thank you for choosing <strong>Joz Tembo Tours &amp; Safari</strong>.
                   We have successfully received your transfer request.</p>
                <div class="highlight-box" style="text-align:center;">
                    <p style="margin:0 0 6px;color:#777;font-size:12px;text-transform:uppercase;letter-spacing:1px;">
                        Your reference number
                    </p>
                    <strong style="font-size:22px;letter-spacing:1px;">{reference_code}</strong>
                </div>
                <h3 style="margin-bottom:10px;">Your request summary</h3>
                {details_html}
                <p style="margin-top:22px;">Our team will review your request and contact you to confirm availability and pricing.</p>
                <p>We look forward to making your journey comfortable and seamless.</p>
                <p style="margin-bottom:0;"><strong>Joz Tembo Tours &amp; Safari</strong></p>
            """
            customer_message = Message(
                subject=f"Transfer request received — {reference_code}",
                recipients=[email]
            )
            if sender:
                customer_message.sender = sender
            set_email_html(customer_message, "We’ve Received Your Request", customer_html)
            with mail.connect() as connection:
                connection.send(admin_message)
                connection.send(customer_message)

            app.logger.info("Quick transfer inquiry %s received from %s", reference_code, email)
            return jsonify({
                "success": True,
                "message": "Your transfer request has been received successfully.",
                "reference_code": reference_code
            }), 201

        except Exception as e:
            app.logger.exception("Quick transfer inquiry email failed")
            return jsonify({
                "success": False,
                "error": "We could not send your request right now. Please try again or contact us directly."
            }), 500

    @app.route("/api/inquiries", methods=["POST"])
    @optional_auth
    def create_inquiry():
        try:
            data = request.get_json() or {}
            for field in ('type', 'item_id', 'name', 'email'):
                if not data.get(field): return jsonify({'success': False, 'error': f'{field} is required'}), 400
            email_pattern = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
            if not email_pattern.match(str(data['email'])): return jsonify({'success': False, 'error': 'Invalid email format'}), 400

            ref_prefix = {'package': 'PKG', 'excursion': 'EXC', 'transfer': 'TRF'}.get(data['type'], 'INQ')
            reference_code = f"JT-{ref_prefix}-{uuid.uuid4().hex[:6].upper()}"

            unit_price = 0.0
            currency = 'USD'
            resolved_name = data.get('item_name') or 'N/A'
            pricing_label = None
            if data['type'] == 'excursion':
                exc = Excursion.query.filter_by(id=data['item_id'], is_active=True).first()
                if not exc: return jsonify({'success': False, 'error': 'Excursion not found'}), 404
                unit_price = float(exc.price or 0)
                currency = exc.price_currency or 'USD'
                resolved_name = exc.name
            elif data['type'] == 'package':
                pkg = Package.query.filter_by(id=data['item_id'], is_active=True).first()
                if not pkg: return jsonify({'success': False, 'error': 'Package not found'}), 404
                resolved_name = pkg.name
                p = pkg.prices or {}
                pax_n = max(1, int(data.get('pax') or 1))
                tier_rates = {2: p.get('pax_2'), 4: p.get('pax_4'), 6: p.get('pax_6')}
                chosen_rate = None
                if pax_n <= 2 and tier_rates[2]:
                    chosen_rate = float(tier_rates[2])
                    pricing_label = f"2-pax rate ({currency} {chosen_rate:,.0f}/person)"
                elif pax_n <= 4 and (tier_rates[4] or tier_rates[2]):
                    r = tier_rates[4] or tier_rates[2]
                    chosen_rate = float(r)
                    pricing_label = f"4-pax rate ({currency} {chosen_rate:,.0f}/person)"
                elif pax_n <= 6 and (tier_rates[6] or tier_rates[4] or tier_rates[2]):
                    r = tier_rates[6] or tier_rates[4] or tier_rates[2]
                    chosen_rate = float(r)
                    pricing_label = f"6-pax rate ({currency} {chosen_rate:,.0f}/person)"
                else: pricing_label = "NEGOTIABLE — custom group rate"
                unit_price = chosen_rate if chosen_rate else 0.0
            elif data['type'] == 'transfer':
                t = Transfer.query.filter_by(id=data['item_id'], is_active=True).first()
                if not t: return jsonify({'success': False, 'error': 'Transfer route not found'}), 404
                resolved_name = t.route
                currency = t.currency or 'EUR'
                pax_n = max(1, int(data.get('pax') or 1))
                if pax_n <= 2: chosen_rate = t.price_2_pax
                elif pax_n <= 4: chosen_rate = t.price_4_pax or t.price_2_pax
                else: chosen_rate = t.price_6_pax or t.price_4_pax or t.price_2_pax
                unit_price = float(chosen_rate or 0)
                pricing_label = f"Transfer tier for {pax_n} pax"
            else: return jsonify({'success': False, 'error': 'Invalid inquiry type'}), 400

            pax = max(1, int(data.get('pax') or 1))
            price_estimate = unit_price * pax
            admin_recipient = app.config.get('MAIL_ADMIN_RECIPIENT', 'info@joztembotours.com')
            sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')

            admin_html = f"""
            <h3>New {data['type'].title()} Inquiry</h3>
            <p><strong>Reference:</strong> {reference_code}</p>
            <p><strong>Item:</strong> {resolved_name}</p>
            <p><strong>Name:</strong> {data['name']}</p>
            <p><strong>Email:</strong> {data['email']}</p>
            <p><strong>Phone:</strong> {data.get('phone', '—')}</p>
            <p><strong>Pax:</strong> {pax}</p>
            <p><strong>Pricing:</strong> {pricing_label or 'n/a'}</p>
            <div class="highlight-box">
                <p><strong>Estimated Price:</strong> {price_estimate:,.2f} {currency}</p>
            </div>
            <p><strong>Message:</strong></p>
            <p style="color: #666; font-style: italic;">"{data.get('message', 'No message provided.')}"</p>
            """
            msg = Message(subject=f"[{reference_code}] New {data['type'].title()} Inquiry — {resolved_name}", recipients=[admin_recipient])
            if sender: msg.sender = sender
            if data.get('email'): msg.reply_to = data['email']
            set_email_html(msg, f"Inquiry: {reference_code}", admin_html)
            customer_html = f"""
            <h3>Hi {data['name']},</h3>
            <p>Our team is currently working on your inquiry for <strong>{resolved_name}</strong>. We will get back to you shortly with more details.</p>
            <div class="highlight-box">
                <p><strong>Reference Number:</strong> {reference_code}</p>
                <p><strong>Route/Item:</strong> {resolved_name}</p>
                <p><strong>Estimated Price:</strong> {price_estimate:,.2f} {currency}</p>
                <p><strong>Pricing Details:</strong> {pricing_label or 'Custom Group Rate'}</p>
            </div>
            <p>Thank you for considering Joz Tembo Tours for your safari adventure.</p>
            """
            confirm = Message(subject=f"We received your inquiry — {reference_code}", recipients=[data['email']])
            if sender: confirm.sender = sender
            set_email_html(confirm, "Inquiry Received", customer_html)
            with mail.connect() as conn:
                conn.send(msg)
                conn.send(confirm)

            return jsonify({
                'success': True,
                'message': 'Inquiry received. We will contact you shortly.',
                'reference_code': reference_code,
                'price_estimate': price_estimate,
                'currency': currency,
                'pax': pax,
                'pricing_label': pricing_label
            }), 201
        except Exception as e:
            app.logger.error(f"Inquiry error: {e}")
            return jsonify({'success': False, 'error': f'Inquiry failed: {str(e)}'}), 500

    @app.route("/")
    def index():
        return jsonify({"message": "🦁 Joztembo Tours - Backend API", "version": "3.8"}), 200


load_dotenv()

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)