---
name: dev-utils
description: "Developer utility toolkit: QR code decode/generate, Base64 encode/decode, hash calculation, URL encode/decode, JWT decode, JSON/XML formatting, AES encrypt/decrypt. Use when: user asks to decode a QR code image, encode/decode base64, calculate hash (md5/sha256/etc.), URL encode/decode, decode JWT tokens, format/prettify JSON or XML, or AES encrypt/decrypt data."
---

# Dev Utils

A collection of common developer utilities. All tools use Python and run inline via Bash.

## Dependencies

Install missing packages on first use with `uv pip install`:
- `opencv-python-headless` — QR code decoding
- `qrcode[pil]` — QR code generation
- `pycryptodome` — AES encryption/decryption

All other features use Python standard library only.

## QR Code

### Decode from image

```python
import cv2
img = cv2.imread("image.jpg")
detector = cv2.QRCodeDetector()
data, bbox, _ = detector.detectAndDecode(img)
print(data)
```

### Generate QR code

```python
import qrcode
img = qrcode.make("https://example.com")
img.save("tmp/qrcode.png")
```

Send the generated image to the user with `[SEND_FILE:/absolute/path/to/tmp/qrcode.png]`.

## Base64

### Encode

```python
import base64
# Text
base64.b64encode("hello".encode()).decode()
# File
with open("file.bin", "rb") as f:
    base64.b64encode(f.read()).decode()
```

### Decode

```python
import base64
# Text
base64.b64decode("aGVsbG8=").decode()
# Save to file
with open("output.bin", "wb") as f:
    f.write(base64.b64decode(encoded_str))
```

## Hash

Supported algorithms: md5, sha1, sha224, sha256, sha384, sha512, sha3_256, sha3_512, blake2b, blake2s

All available via Python `hashlib` (standard library).

```python
import hashlib
data = "hello world"

print("MD5:      ", hashlib.md5(data.encode()).hexdigest())
print("SHA1:     ", hashlib.sha1(data.encode()).hexdigest())
print("SHA256:   ", hashlib.sha256(data.encode()).hexdigest())
print("SHA512:   ", hashlib.sha512(data.encode()).hexdigest())
print("SHA3-256: ", hashlib.sha3_256(data.encode()).hexdigest())
print("BLAKE2b:  ", hashlib.blake2b(data.encode()).hexdigest())
```

File hash:
```python
with open("file.bin", "rb") as f:
    print(hashlib.sha256(f.read()).hexdigest())
```

Default output: MD5 + SHA256 when the user does not specify an algorithm.

## URL Encode / Decode

```python
from urllib.parse import quote, unquote, urlencode, parse_qs

# Encode
quote("hello world")            # → hello%20world
urlencode({"key": "a&b=c"})    # → key=a%26b%3Dc

# Decode
unquote("%E4%BD%A0%E5%A5%BD")  # → decoded text
parse_qs("a=1&b=2")            # → {'a': ['1'], 'b': ['2']}
```

## JWT Decode

Decode only (no signature verification). Uses standard library.

```python
import base64, json

def decode_jwt(token):
    parts = token.split(".")
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    return {"header": header, "payload": payload}

result = decode_jwt("eyJhbGci...")
print(json.dumps(result, indent=2, ensure_ascii=False))
```

## JSON Formatting

```python
import json

# Format string
raw = '{"a":1,"b":[2,3],"c":{"d":4}}'
print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))

# Format file
with open("input.json") as f:
    data = json.load(f)
with open("output.json", "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

## XML Formatting

```python
import xml.dom.minidom

# Format string
raw = '<root><a>1</a><b><c>2</c></b></root>'
dom = xml.dom.minidom.parseString(raw)
print(dom.toprettyxml(indent="  "))

# Format file
with open("input.xml") as f:
    dom = xml.dom.minidom.parseString(f.read())
with open("output.xml", "w") as f:
    f.write(dom.toprettyxml(indent="  "))
```

## AES Encrypt / Decrypt

Requires `pycryptodome`. Supports AES-CBC and AES-GCM modes.
Default to AES-GCM (authenticated encryption) when the user does not specify a mode.
Key input accepts hex string or plaintext (plaintext is hashed with SHA-256 to derive the key).

### AES-GCM (default, recommended)

```python
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import base64

def aes_gcm_encrypt(plaintext: str, key: str) -> dict:
    """key: hex string (32/48/64 hex chars = 128/192/256 bit)"""
    key_bytes = bytes.fromhex(key)
    nonce = get_random_bytes(12)
    cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(plaintext.encode())
    return {
        "ciphertext": base64.b64encode(ct).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "tag": base64.b64encode(tag).decode()
    }

def aes_gcm_decrypt(ciphertext_b64: str, key: str, nonce_b64: str, tag_b64: str) -> str:
    key_bytes = bytes.fromhex(key)
    nonce = base64.b64decode(nonce_b64)
    ct = base64.b64decode(ciphertext_b64)
    tag = base64.b64decode(tag_b64)
    cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag).decode()
```

### AES-CBC

```python
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
import base64

def aes_cbc_encrypt(plaintext: str, key: str) -> dict:
    """key: hex string (32/48/64 hex chars = 128/192/256 bit)"""
    key_bytes = bytes.fromhex(key)
    iv = get_random_bytes(16)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plaintext.encode(), AES.block_size))
    return {
        "ciphertext": base64.b64encode(ct).decode(),
        "iv": base64.b64encode(iv).decode()
    }

def aes_cbc_decrypt(ciphertext_b64: str, key: str, iv_b64: str) -> str:
    key_bytes = bytes.fromhex(key)
    iv = base64.b64decode(iv_b64)
    ct = base64.b64decode(ciphertext_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode()
```

### Key from plaintext

When the user provides a plaintext password instead of a hex key:
```python
import hashlib
key_hex = hashlib.sha256("my-password".encode()).hexdigest()  # 64 hex chars = 256 bit
```

## Usage Guidelines

- Display results directly in the reply; do not save to file unless requested
- QR code images: save to `tmp/` and send with `[SEND_FILE:...]`
- User-uploaded files are typically in `tmp/`
