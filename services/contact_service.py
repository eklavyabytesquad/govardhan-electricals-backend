"""Contact / quote-request enquiries (table: enquiries)."""
import supabase
from errors import ApiError


def submit(name, phone, email, message):
    name, phone = str(name or "").strip(), str(phone or "").strip()
    email, message = str(email or "").strip(), str(message or "").strip()
    if not name or not phone or not message:
        raise ApiError(400, "Name, phone and message are required")
    supabase.insert("enquiries", {"name": name, "phone": phone, "email": email or None, "message": message})
