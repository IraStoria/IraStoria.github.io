# © 2026 IraStoria. One-shot TOTP secret generator for the pool worker.
#
# Run:  python worker/totp_setup.py
# Prints a fresh base32 secret, the otpauth:// URI, and the path of a small
# HTML page (in %TEMP%) that shows the same URI as a QR code so it can be
# scanned with Google Authenticator / 1Password / Authy. Paste the secret into
# Cloudflare as the `TOTP_SECRET` secret. Standard library only; sends nothing.

import base64
import html
import json
import os
import secrets
import tempfile
import urllib.parse

ISSUER = "IraStoria pool"
ACCOUNT = "IraStoria"
QR_LIB = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"


def main():
    # 20 random bytes = 160-bit key (RFC 4226 recommended length) -> 32 base32 chars, no padding.
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    # Key URI format (as read by Google Authenticator / 1Password): label is
    # "issuer:account", spaces as %20 (not '+'), colon kept literal.
    label = f"{urllib.parse.quote(ISSUER)}:{urllib.parse.quote(ACCOUNT)}"
    query = urllib.parse.urlencode(
        {"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": "6", "period": "30"},
        quote_via=urllib.parse.quote,
    )
    uri = f"otpauth://totp/{label}?{query}"

    out_dir = os.environ.get("TEMP") or tempfile.gettempdir()
    path = os.path.join(out_dir, "totp_setup.html")
    page = f"""<!doctype html>
<meta charset="utf-8">
<title>TOTP setup - {html.escape(ISSUER)}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 40px auto; max-width: 560px; color: #222; }}
  code {{ font-size: 15px; word-break: break-all; }}
  #qr {{ margin: 24px 0; }}
</style>
<h1>{html.escape(ISSUER)}</h1>
<p>Scan with Google Authenticator / 1Password, then put the same secret into Cloudflare as <code>TOTP_SECRET</code>.</p>
<div id="qr"></div>
<p>Secret (base32): <code id="secret"></code></p>
<p>URI: <code id="uri"></code></p>
<p><small>Delete this file when done. Nothing on this page is sent anywhere.</small></p>
<script src="{QR_LIB}"></script>
<script>
  var uri = {json.dumps(uri)};
  var secret = {json.dumps(secret)};
  document.getElementById('secret').textContent = secret;
  document.getElementById('uri').textContent = uri;
  if (window.QRCode) new QRCode(document.getElementById('qr'), {{ text: uri, width: 256, height: 256 }});
  else document.getElementById('qr').textContent = '(QR library did not load; enter the secret manually)';
</script>
"""
    data = page.encode("utf-8")  # encode first so a failure never leaves a truncated file
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    print(secret)
    print(uri)
    print(path)


if __name__ == "__main__":
    main()
