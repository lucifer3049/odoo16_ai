f = '/usr/lib/python3/dist-packages/urllib3/contrib/pyopenssl.py'
t = open(f).read()
old = 'from cryptography.hazmat.backends.openssl.x509 import _Certificate'
new = (
    'try:\n'
    '    from cryptography.hazmat.backends.openssl.x509 import _Certificate\n'
    'except ImportError:\n'
    '    _Certificate = None'
)
open(f, 'w').write(t.replace(old, new))
print('urllib3 pyopenssl.py patched OK')
