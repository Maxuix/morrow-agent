#!/bin/sh
set -eu
if /opt/morrow/venv/bin/python -c 'import http.client; connection=http.client.HTTPConnection("127.0.0.1", 18765, timeout=5); connection.request("GET", "/"); response=connection.getresponse(); assert response.status == 200 and response.read() == b"G1_SERVICE_OK"'; then
  printf '1\n' > /logs/verifier/reward.txt
else
  printf '0\n' > /logs/verifier/reward.txt
fi
