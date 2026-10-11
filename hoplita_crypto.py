
import json
import time
import base64

from nacl.public import PrivateKey, PublicKey, SealedBox, Box
from nacl.encoding import Base64Encoder


ENVELOPE_VERSION = 1



def generate_identity():
    sk = PrivateKey.generate()
    priv_b64 = sk.encode(Base64Encoder).decode("utf-8")
    pub_b64 = sk.public_key.encode(Base64Encoder).decode("utf-8")
    return priv_b64, pub_b64



def make_envelope(resource_type, payload, sender_pub_b64=None, meta=None):
    return {
        "v": ENVELOPE_VERSION,
        "type": resource_type,
        "ts": int(time.time()),
        "sender_pub": sender_pub_b64,
        "meta": meta or {},
        "payload": payload,
    }



def seal_for(recipient_pub_b64, envelope):
    pub = PublicKey(recipient_pub_b64.encode("utf-8"), Base64Encoder)
    sealed = SealedBox(pub)
    plaintext = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    ciphertext = sealed.encrypt(plaintext)
    return base64.b64encode(ciphertext).decode("utf-8")


def unseal(my_priv_b64, blob_b64):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    unseal_box = SealedBox(sk)
    ciphertext = base64.b64decode(blob_b64)
    plaintext = unseal_box.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8"))



def box_for(my_priv_b64, recipient_pub_b64, envelope):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    pub = PublicKey(recipient_pub_b64.encode("utf-8"), Base64Encoder)
    box = Box(sk, pub)
    plaintext = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    encrypted = box.encrypt(plaintext)
    return base64.b64encode(bytes(encrypted)).decode("utf-8")


def unbox(my_priv_b64, sender_pub_b64, blob_b64):
    sk = PrivateKey(my_priv_b64.encode("utf-8"), Base64Encoder)
    pub = PublicKey(sender_pub_b64.encode("utf-8"), Base64Encoder)
    box = Box(sk, pub)
    ciphertext = base64.b64decode(blob_b64)
    plaintext = box.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8"))



def fingerprint(pub_b64):
    import hashlib
    raw = base64.b64decode(pub_b64)
    h = hashlib.sha256(raw).hexdigest().upper()
    groups = [h[i:i+4] for i in range(0, 20, 4)]
    return " ".join(groups)
