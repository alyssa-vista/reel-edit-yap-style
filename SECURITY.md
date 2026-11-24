# Security

Please report security problems privately: open the **Security** tab of this repository and choose **Report a vulnerability**. Do not open a public issue for them.

Only the newest release is supported.

What to expect from this kit: the survey server listens on 127.0.0.1 only, needs a one-time token in the address, checks the Host and Origin headers, and stops after Save or 30 minutes. `find.py` only makes https requests, with certificates checked. No script needs an administrator password.
