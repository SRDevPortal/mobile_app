"""Attach profile photo to Mobile App User (`image` field) for Desk sidebar + API workflows."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import frappe
import requests
from frappe import _
from frappe.exceptions import AuthenticationError
from frappe.utils.file_manager import save_file

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MiB


def _sync_mobile_app_user_image_field(doc_name: str, file_doc) -> str | None:
	"""Persist ``image`` on the parent doc. Core ``save_file`` creates ``File`` but does not set Attach Image fields."""
	if not file_doc:
		return None
	url = getattr(file_doc, "file_url", None) or ""
	if not url:
		return None
	frappe.db.set_value("File", file_doc.name, "is_private", 0)
	frappe.db.set_value("Mobile App User", doc_name, "image", url)
	from mobile_app.realtime import notify_change
	notify_change(frappe._dict(doctype="Mobile App User"))
	return url


def _is_image_bytes(body: bytes) -> bool:
	if len(body) < 12:
		return False
	if body.startswith(b"\xff\xd8\xff"):
		return True
	if body.startswith(b"\x89PNG\r\n\x1a\n"):
		return True
	if body.startswith(b"GIF87a") or body.startswith(b"GIF89a"):
		return True
	if body.startswith(b"RIFF") and b"WEBP" in body[:16]:
		return True
	return False


def _resolve_mobile_app_user_name() -> str | None:
	"""Match ``mobile_app.api.v1._find_user_name`` lookup from form fields."""
	from mobile_app.api.v1 import _find_user_name

	p = dict(frappe.local.form_dict or {})
	name = _find_user_name(p)
	if name:
		return name
	un = (p.get("user_name") or "").strip()
	if un and frappe.db.exists("Mobile App User", un):
		return un
	return None


@frappe.whitelist(methods=["POST"])
def upload_profile_image():
	"""Upload an image file and attach it to ``Mobile App User.image`` (public file).

	**Auth:** standard Frappe API credentials only (no custom ERP header)::

	    Authorization: token <api_key>:<api_secret>

	In Desk these are **API Key** and **API Secret** (**My Settings → API Access**). The user must be allowed
	to write **Mobile App User**.

	**Body:** ``multipart/form-data`` — field ``file`` (image), plus user lookup fields as form fields.

	Example::

	    curl -X POST "http://localhost:8000/api/method/mobile_app.api.profile_image.upload_profile_image" \\
	      -H "Authorization: token YOUR_KEY:YOUR_SECRET" \\
	      -F "file=@/path/to/photo.jpg" \\
	      -F "supabase_user_id=b41b0af8-97c9-4ffb-96d5-7d2a6c639616"
	"""
	if frappe.session.user == "Guest":
		frappe.throw(
			_("Log in with API credentials: Authorization: token <api_key>:<api_secret>"),
			AuthenticationError,
		)

	files = frappe.request.files
	if not files:
		frappe.throw(_("No file uploaded"))

	fstorage = files.get("file")
	if not fstorage or not getattr(fstorage, "filename", None):
		frappe.throw(_("Attach the image as form field \"file\""))

	content = fstorage.stream.read()
	if len(content) > MAX_IMAGE_BYTES:
		frappe.throw(_("Image is too large (max {0} MB)").format(MAX_IMAGE_BYTES // (1024 * 1024)))
	if len(content) < 50:
		frappe.throw(_("File is too small to be a valid image"))

	if not _is_image_bytes(content):
		frappe.throw(_("File must be a JPEG, PNG, GIF, or WebP image"))

	user_name = _resolve_mobile_app_user_name()
	if not user_name:
		frappe.throw(
			_(
				"Could not resolve Mobile App User: pass external_id, email, phone, supabase_user_id, "
				"customer_id, id, or user_name"
			)
		)

	raw_name = fstorage.filename or "avatar.jpg"
	base = os.path.basename(raw_name.replace("\\", "/"))
	if not base or base in (".", ".."):
		base = "avatar.jpg"
	if "." not in base:
		base = f"{base}.jpg"

	doc = frappe.get_doc("Mobile App User", user_name)
	doc.check_permission("write")
	file_doc = save_file(base, content, doc.doctype, doc.name, is_private=0, df="image")
	path = _sync_mobile_app_user_image_field(doc.name, file_doc)
	doc.reload()
	if not path:
		path = doc.image
	out = {
		"success": True,
		"name": doc.name,
		"image": path,
		"profile_image_url": frappe.utils.get_url(path) if path else None,
	}
	return out


@frappe.whitelist()
def set_profile_image_from_url(user_name: str, image_url: str):
	"""Download an image from HTTPS and save it on ``Mobile App User.image``.

	:param user_name: Document name of Mobile App User (usually ``external_id``).
	:param image_url: Public ``https://`` URL of an image (JPEG/PNG/GIF/WebP).

	Auth: normal Frappe session, or API key::

	    Authorization: token <api_key>:<api_secret>

	Example::

	    POST /api/method/mobile_app.api.profile_image.set_profile_image_from_url
	    Content-Type: application/x-www-form-urlencoded

	    user_name=b41b0af8-...&image_url=https://example.com/a.jpg
	"""
	if not user_name or not image_url:
		frappe.throw(_("user_name and image_url are required"))

	image_url = image_url.strip()
	if not image_url.startswith(("http://", "https://")):
		frappe.throw(_("image_url must start with http:// or https://"))

	doc = frappe.get_doc("Mobile App User", user_name)
	doc.check_permission("write")

	try:
		resp = requests.get(image_url, timeout=60)
		resp.raise_for_status()
	except requests.RequestException as e:
		frappe.throw(_("Could not download image: {0}").format(str(e)))

	content = resp.content
	if len(content) < 50:
		frappe.throw(_("Downloaded file is too small to be a valid image"))

	ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
	if ct and not ct.startswith("image/") and ct != "application/octet-stream":
		frappe.throw(_("URL did not return an image (Content-Type: {0})").format(ct))

	if not ct.startswith("image/") and not _is_image_bytes(content):
		frappe.throw(_("Downloaded bytes do not look like a JPEG, PNG, GIF, or WebP image"))

	path = urlparse(image_url).path or ""
	base = path.rsplit("/", 1)[-1] if path else ""
	if not base or "." not in base:
		ext = ".jpg"
		if "png" in ct:
			ext = ".png"
		elif "webp" in ct:
			ext = ".webp"
		elif "gif" in ct:
			ext = ".gif"
		base = f"avatar-{frappe.generate_hash(length=8)}{ext}"

	file_doc = save_file(base, content, doc.doctype, doc.name, is_private=0, df="image")
	_sync_mobile_app_user_image_field(doc.name, file_doc)
	doc.reload()
	return {"success": True, "image": doc.image}
