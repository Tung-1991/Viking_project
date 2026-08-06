import hmac
import hashlib
import base64
import uuid
import datetime

def generate_nonce():
    return uuid.uuid4().hex

def generate_date():
    # RFC 1123 format
    return datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

def url_encode_base64(b64_str):
    return b64_str.replace('+', '%2B').replace('/', '%2F').replace('=', '%3D')

def generate_signature_header(api_key, api_secret, method, path):
    """
    Generates the X-Signature and Date headers for DNSE OpenAPI.
    """
    method = method.lower()
    date_str = generate_date()
    nonce = generate_nonce()
    
    # Building signing string
    # (request-target): get /accounts
    # x-aux-date: ...
    # nonce: ...
    signing_string = f"(request-target): {method} {path}\nx-aux-date: {date_str}\nnonce: {nonce}"
    
    # HMAC SHA256
    secret_bytes = api_secret.encode('utf-8')
    signing_bytes = signing_string.encode('utf-8')
    
    signature = hmac.new(secret_bytes, signing_bytes, hashlib.sha256).digest()
    b64_signature = base64.b64encode(signature).decode('utf-8')
    encoded_signature = url_encode_base64(b64_signature)
    
    x_signature = f'Signature keyId="{api_key}",algorithm="hmac-sha256",headers="(request-target) x-aux-date",signature="{encoded_signature}",nonce="{nonce}"'
    
    return x_signature, date_str
