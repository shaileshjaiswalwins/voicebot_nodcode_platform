#!/bin/bash

# Your credentials
HOST="192.168.29.196"
USER="sunnys_004098"
PASS="SunnY@123$987"

# Python script that feeds the password to the SSH prompt
python3 -c "
import pty, os, sys
def read(fd):
    data = os.read(fd, 1024)
    if b'password:' in data.lower():
        os.write(fd, b'$PASS\n')
    return data

pty.spawn(['ssh', '${USER}@${HOST}'], read)
"
