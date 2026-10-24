# models.py - Complete models with Admin Hierarchy, Reviews, Packages, Excursions, Transfers, Blog, About
from datetime import datetime
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from time import time
from flask import current_app
import json


# ============ ADMIN ROLE ENUM ============
class AdminRole:
    SUPER_ADMIN = 'super_admin'
    COORDINATOR = 'coordinator'
    CONTENT_MANAGER = 'content_manager'
    REVIEW_MODERATOR = 'review_moderator'

    ALL = [SUPER_ADMIN, COORDINATOR, CONTENT_MANAGER, REVIEW_MODERATOR]


# ============ USER MODEL WITH ADMIN HIERARCHY ============

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(20), default='')
    avatar_url = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    is_admin = db.Column(db.Boolean, default=False)
    admin_role = db.Column(db.String(30), nullable=True)
    admin_order = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by = db.relationship('User', remote_side=[id], backref='created_admins')

    comments = db.relationship('Comment', backref='author', lazy=True,
                                foreign_keys='Comment.user_id')
    stories = db.relationship('Story', backref='author', lazy=True)

    customer_reviews = db.relationship('CustomerReview', backref='user', lazy='dynamic',
                                        foreign_keys='CustomerReview.user_id')
    review_replies = db.relationship('ReviewReply', backref='user', lazy='dynamic',
                                      foreign_keys='ReviewReply.user_id')
    review_media = db.relationship('ReviewMedia', backref='uploader', lazy='dynamic',
                                    foreign_keys='ReviewMedia.uploaded_by_id')

    created_packages = db.relationship('Package', backref='creator', lazy=True,
                                        foreign_keys='Package.created_by_id')
    created_excursions = db.relationship('Excursion', backref='creator', lazy=True,
                                          foreign_keys='Excursion.created_by_id')
    created_transfers = db.relationship('Transfer', backref='creator', lazy=True,
                                         foreign_keys='Transfer.created_by_id')
    created_blog_posts = db.relationship('BlogPost', backref='author', lazy=True,
                                          foreign_keys='BlogPost.author_id')

    admin_actions = db.relationship('AdminActionLog', backref='admin', lazy=True,
                                     foreign_keys='AdminActionLog.admin_id')

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)
        if self.email and not self.username:
            self.username = self.email.split('@')[0]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_auth_token(self, expires_in=604800):
        return jwt.encode(
            {'user_id': self.id, 'exp': time() + expires_in},
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )

    @staticmethod
    def verify_auth_token(token):
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            return User.query.get(data['user_id'])
        except Exception:
            return None

    def is_super_admin(self):
        return self.is_admin and self.admin_role == AdminRole.SUPER_ADMIN

    def is_coordinator(self):
        return self.is_admin and self.admin_role == AdminRole.COORDINATOR

    def is_content_manager(self):
        return self.is_admin and self.admin_role == AdminRole.CONTENT_MANAGER

    def is_review_moderator(self):
        return self.is_admin and self.admin_role == AdminRole.REVIEW_MODERATOR

    def can_impersonate(self):
        return self.is_super_admin()

    def can_manage_admins(self):
        return self.is_super_admin()

    def can_manage_users(self):
        return self.is_super_admin() or self.is_coordinator()

    def can_manage_packages(self):
        return self.is_super_admin() or self.is_coordinator() or self.is_content_manager()

    def can_manage_excursions(self):
        return self.is_super_admin() or self.is_coordinator() or self.is_content_manager()

    def can_manage_blog(self):
        return self.is_super_admin() or self.is_coordinator() or self.is_content_manager()

    def can_manage_about(self):
        return self.is_super_admin() or self.is_coordinator() or self.is_content_manager()

    def can_moderate_reviews(self):
        return self.is_admin

    def can_delete_review(self):
        return self.is_admin

    def can_edit_any_review(self):
        return self.is_admin

    def can_delete_any_reply(self):
        return self.is_admin

    def can_view_admin_dashboard(self):
        return self.is_admin

    def get_permissions(self):
        return {
            'can_impersonate': self.can_impersonate(),
            'can_manage_admins': self.can_manage_admins(),
            'can_manage_users': self.can_manage_users(),
            'can_manage_packages': self.can_manage_packages(),
            'can_manage_excursions': self.can_manage_excursions(),
            'can_manage_blog': self.can_manage_blog(),
            'can_manage_about': self.can_manage_about(),
            'can_moderate_reviews': self.can_moderate_reviews(),
            'can_delete_review': self.can_delete_review(),
            'can_edit_any_review': self.can_edit_any_review(),
            'can_delete_any_reply': self.can_delete_any_reply(),
            'can_view_admin_dashboard': self.can_view_admin_dashboard()
        }

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'username': self.username,
            'email': self.email,
            'phone': self.phone,
            'avatar_url': self.avatar_url,
            'is_admin': self.is_admin,
            'admin_role': self.admin_role,
            'admin_order': self.admin_order,
            'is_active': self.is_active,
            'permissions': self.get_permissions() if self.is_admin else {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_admin_dict(self):
        data = self.to_dict()
        data.update({
            'review_count': self.customer_reviews.filter_by(is_active=True).count(),
            'reply_count': self.review_replies.filter_by(is_active=True).count(),
            'created_by': self.created_by.to_dict() if self.created_by else None
        })
        return data

    def __repr__(self):
        return f'<User {self.username or self.email} ({self.admin_role or "user"})>'


# ============ ADMIN ACTION LOG ============

class AdminActionLog(db.Model):
    __tablename__ = 'admin_action_logs'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    resource_type = db.Column(db.String(50), nullable=False)
    resource_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.JSON)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'admin_id': self.admin_id,
            'admin_name': self.admin.name if self.admin else 'Unknown',
            'action_type': self.action_type,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'details': self.details,
            'ip_address': self.ip_address,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ============ IMPERSONATION SESSION ============

class ImpersonationSession(db.Model):
    __tablename__ = 'impersonation_sessions'

    id = db.Column(db.Integer, primary_key=True)
    super_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    impersonated_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(500), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    super_admin = db.relationship('User', foreign_keys=[super_admin_id])
    impersonated_user = db.relationship('User', foreign_keys=[impersonated_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'super_admin': self.super_admin.to_dict() if self.super_admin else None,
            'impersonated_user': self.impersonated_user.to_dict() if self.impersonated_user else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'is_active': self.is_active
        }


# ============ CUSTOMER REVIEW SYSTEM ============

class CustomerReview(db.Model):
    __tablename__ = 'customer_reviews'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewer_name = db.Column(db.String(100), nullable=True)
    reviewer_email = db.Column(db.String(120), nullable=True)
    rating = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    visit_date = db.Column(db.Date, nullable=True)
    package_used = db.Column(db.String(200), nullable=True)

    status = db.Column(db.String(20), default='approved')
    admin_notes = db.Column(db.Text, nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)

    is_edited = db.Column(db.Boolean, default=False)
    edited_at = db.Column(db.DateTime, nullable=True)
    edited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    replies = db.relationship('ReviewReply', backref='review', lazy='dynamic',
                              cascade='all, delete-orphan',
                              order_by='ReviewReply.created_at.asc()')
    media = db.relationship('ReviewMedia', backref='review', lazy='dynamic',
                            cascade='all, delete-orphan')
    approver = db.relationship('User', foreign_keys=[approved_by])
    editor = db.relationship('User', foreign_keys=[edited_by_id])

    def to_dict(self, include_user=True, include_media=True):
        data = {
            'id': self.id,
            'reviewer_name': self.reviewer_name,
            'rating': self.rating,
            'title': self.title,
            'content': self.content,
            'visit_date': self.visit_date.isoformat() if self.visit_date else None,
            'package_used': self.package_used,
            'status': self.status,
            'admin_notes': self.admin_notes,
            'is_edited': self.is_edited,
            'edited_at': self.edited_at.isoformat() if self.edited_at else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'replies_count': self.replies.filter_by(is_active=True).count(),
            'media_count': self.media.count()
        }

        if include_media:
            data['media'] = [m.to_dict() for m in self.media.all()]

        data['replies'] = [reply.to_dict() for reply in self.replies.filter_by(is_active=True)]

        if include_user:
            if self.user:
                data['user'] = {
                    'id': self.user.id,
                    'name': self.user.name,
                    'username': self.user.username,
                    'avatar': self.user.avatar_url
                }
            else:
                data['user'] = None

        if self.approver:
            data['approver'] = {'id': self.approver.id, 'name': self.approver.name}
        else:
            data['approver'] = None

        return data

    def get_display_name(self):
        if self.user:
            return self.user.name
        elif self.reviewer_name:
            return self.reviewer_name
        return "Anonymous"

    def __repr__(self):
        return f'<CustomerReview {self.id} - {self.status}>'


class ReviewMedia(db.Model):
    __tablename__ = 'review_media'

    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('customer_reviews.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)
    file_url = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'review_id': self.review_id,
            'media_type': self.media_type,
            'file_url': self.file_url,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ReviewReply(db.Model):
    __tablename__ = 'review_replies'

    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('customer_reviews.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_edited = db.Column(db.Boolean, default=False)
    edited_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'review_id': self.review_id,
            'content': self.content,
            'is_edited': self.is_edited,
            'edited_at': self.edited_at.isoformat() if self.edited_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'user': {
                'id': self.user.id,
                'name': self.user.name,
                'username': self.user.username,
                'is_admin': self.user.is_admin,
                'admin_role': self.user.admin_role
            } if self.user else None
        }


# ============ PACKAGE SYSTEM ============

class PackageCategory(db.Model):
    __tablename__ = 'package_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    packages = db.relationship('Package', backref='category', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'display_order': self.display_order,
            'package_count': len([p for p in self.packages if p.is_active])
        }


class Package(db.Model):
    __tablename__ = 'packages'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=True)
    description = db.Column(db.Text)
    short_description = db.Column(db.String(500))

    total_days = db.Column(db.Integer, nullable=False)
    total_nights = db.Column(db.Integer, nullable=False)

    full_itinerary = db.Column(db.Text, default='')

    category_id = db.Column(db.Integer, db.ForeignKey('package_categories.id'), nullable=True)

    prices = db.Column(db.JSON, default=dict)
    price_notes = db.Column(db.Text, default='')

    includes = db.Column(db.JSON, default=list)
    excludes = db.Column(db.JSON, default=list)

    is_featured = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, default=0)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    media = db.relationship('PackageMedia', backref='package', lazy='dynamic',
                            cascade='all, delete-orphan')
    itinerary_days = db.relationship('PackageDay', backref='package', lazy='dynamic',
                                      cascade='all, delete-orphan',
                                      order_by='PackageDay.day_number')

    def get_main_image(self):
        main_media = self.media.filter_by(is_main=True, media_type='image').first()
        if main_media:
            return main_media.file_url
        first_image = self.media.filter_by(media_type='image').first()
        return first_image.file_url if first_image else None

    def to_dict(self, include_days=True, include_media=True):
        data = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'short_description': self.short_description,
            'total_days': self.total_days,
            'total_nights': self.total_nights,
            'full_itinerary': self.full_itinerary,
            'category': self.category.to_dict() if self.category else None,
            'prices': self.prices or {},
            'price_notes': self.price_notes,
            'includes': self.includes or [],
            'excludes': self.excludes or [],
            'is_featured': self.is_featured,
            'is_active': self.is_active,
            'display_order': self.display_order,
            'main_image': self.get_main_image(),
            'media_count': self.media.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

        if include_days:
            data['itinerary_days'] = [day.to_dict() for day in self.itinerary_days]

        if include_media:
            data['media'] = [m.to_dict() for m in self.media.all()]

        return data

    def __repr__(self):
        return f'<Package {self.name}>'


class PackageDay(db.Model):
    __tablename__ = 'package_days'

    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    day_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    activities = db.Column(db.JSON, default=list)
    meals = db.Column(db.JSON, default=list)
    accommodation = db.Column(db.String(200))
    park_name = db.Column(db.String(100))

    def to_dict(self):
        return {
            'id': self.id,
            'day_number': self.day_number,
            'title': self.title,
            'description': self.description,
            'activities': self.activities or [],
            'meals': self.meals or [],
            'accommodation': self.accommodation,
            'park_name': self.park_name
        }


class PackageMedia(db.Model):
    __tablename__ = 'package_media'

    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)
    file_url = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    is_main = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)
    source = db.Column(db.String(20), default='upload')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'package_id': self.package_id,
            'media_type': self.media_type,
            'file_url': self.file_url,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'is_main': self.is_main,
            'display_order': self.display_order,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ============ EXCURSION SYSTEM ============

class Excursion(db.Model):
    __tablename__ = 'excursions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=True)
    description = db.Column(db.Text)
    short_description = db.Column(db.String(500))

    price = db.Column(db.Float, nullable=False)
    price_currency = db.Column(db.String(10), default='USD')
    price_notes = db.Column(db.Text, default='')

    duration_hours = db.Column(db.Float, nullable=True)
    duration_text = db.Column(db.String(100))

    location = db.Column(db.String(200))

    includes = db.Column(db.JSON, default=list)
    excludes = db.Column(db.JSON, default=list)

    is_featured = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, default=0)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    media = db.relationship('ExcursionMedia', backref='excursion', lazy='dynamic',
                            cascade='all, delete-orphan')

    def get_main_image(self):
        main_media = self.media.filter_by(is_main=True, media_type='image').first()
        if main_media:
            return main_media.file_url
        first_image = self.media.filter_by(media_type='image').first()
        return first_image.file_url if first_image else None

    def to_dict(self, include_media=True):
        data = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'short_description': self.short_description,
            'price': self.price,
            'price_currency': self.price_currency,
            'price_notes': self.price_notes,
            'duration_hours': self.duration_hours,
            'duration_text': self.duration_text,
            'location': self.location,
            'includes': self.includes or [],
            'excludes': self.excludes or [],
            'is_featured': self.is_featured,
            'is_active': self.is_active,
            'display_order': self.display_order,
            'main_image': self.get_main_image(),
            'media_count': self.media.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

        if include_media:
            data['media'] = [m.to_dict() for m in self.media.all()]

        return data

    def __repr__(self):
        return f'<Excursion {self.name}>'


class ExcursionMedia(db.Model):
    __tablename__ = 'excursion_media'

    id = db.Column(db.Integer, primary_key=True)
    excursion_id = db.Column(db.Integer, db.ForeignKey('excursions.id'), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)
    file_url = db.Column(db.String(500), nullable=False)
    file_name = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    is_main = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)
    source = db.Column(db.String(20), default='upload')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'excursion_id': self.excursion_id,
            'media_type': self.media_type,
            'file_url': self.file_url,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'is_main': self.is_main,
            'display_order': self.display_order,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ============ TRANSFER SYSTEM ============

class Transfer(db.Model):
    """Airport / hotel transfer routes with tiered pricing by pax"""
    __tablename__ = 'transfers'

    id = db.Column(db.Integer, primary_key=True)
    route = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=True)
    description = db.Column(db.Text, default='')

    currency = db.Column(db.String(10), default='EUR')

    price_2_pax = db.Column(db.Float, nullable=True)
    price_4_pax = db.Column(db.Float, nullable=True)
    price_6_pax = db.Column(db.Float, nullable=True)

    per_car = db.Column(db.Boolean, default=False)

    is_active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, default=0)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'route': self.route,
            'slug': self.slug,
            'description': self.description,
            'currency': self.currency,
            'prices': {
                '2': self.price_2_pax,
                '4': self.price_4_pax,
                '6': self.price_6_pax,
            },
            'per_car': self.per_car,
            'is_active': self.is_active,
            'display_order': self.display_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<Transfer {self.route}>'


# ============ BLOG SYSTEM (with Community Stories) ============

class BlogPost(db.Model):
    __tablename__ = 'blog_posts'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    slug = db.Column(db.String(300), unique=True, nullable=True)

    excerpt = db.Column(db.Text, default='')
    content = db.Column(db.Text, default='')

    category = db.Column(db.String(100), default='Travel Tips')
    author_name = db.Column(db.String(120), default='Joztembo Tours')
    author_avatar = db.Column(db.String(500), default='')

    cover_image = db.Column(db.String(500), default='')
    cover_image_alt = db.Column(db.String(255), default='')

    tags = db.Column(db.JSON, default=list)

    is_featured = db.Column(db.Boolean, default=False)
    is_published = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, default=0)

    published_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    views = db.Column(db.Integer, default=0)

    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    is_community = db.Column(db.Boolean, default=False, index=True)
    community_token = db.Column(db.String(64), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_content=True):
        data = {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'excerpt': self.excerpt,
            'category': self.category,
            'author_name': self.author_name,
            'author_avatar': self.author_avatar,
            'cover_image': self.cover_image,
            'cover_image_alt': self.cover_image_alt,
            'tags': self.tags or [],
            'is_featured': self.is_featured,
            'is_published': self.is_published,
            'is_active': self.is_active,
            'is_community': self.is_community,
            'display_order': self.display_order,
            'views': self.views or 0,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_content:
            data['content'] = self.content
        return data

    def __repr__(self):
        kind = "Story" if self.is_community else "Post"
        return f'<BlogPost {kind} {self.id} {self.title[:40]}>'


# ============ ABOUT PAGE IMAGES ============

class AboutImage(db.Model):
    __tablename__ = 'about_images'

    id = db.Column(db.Integer, primary_key=True)
    section_key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    image_url = db.Column(db.String(500), nullable=False)
    alt_text = db.Column(db.String(200), default='')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'section_key': self.section_key,
            'image_url': self.image_url,
            'alt_text': self.alt_text,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# ============ ABOUT PAGE PEOPLE (Founders & Team) ============

class AboutPerson(db.Model):
    __tablename__ = 'about_people'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(120), nullable=False)
    person_type = db.Column(db.String(20), nullable=False, index=True)  # 'founder' or 'team'
    image_url = db.Column(db.String(500), nullable=True)
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role,
            'person_type': self.person_type,
            'image_url': self.image_url,
            'display_order': self.display_order,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# ============ LEGACY MODELS ============

class Comment(db.Model):
    __tablename__ = 'comments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'author': self.author.to_dict() if self.author else None
        }


class Story(db.Model):
    __tablename__ = 'stories'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50))
    tags = db.Column(db.JSON)
    is_featured = db.Column(db.Boolean, default=False)
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)


class Park(db.Model):
    __tablename__ = 'parks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    location = db.Column(db.String(200))
    description = db.Column(db.Text)
    known_for = db.Column(db.JSON)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location,
            'description': self.description,
            'known_for': self.known_for or [],
            'is_active': self.is_active
        }