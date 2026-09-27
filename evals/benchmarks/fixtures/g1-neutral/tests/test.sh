#!/bin/sh
set -eu
test -f /opt/morrow/.bench-setup-ok
printf '1\n' > /logs/verifier/reward.txt
