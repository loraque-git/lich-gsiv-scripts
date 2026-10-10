#!/bin/bash
# Tiny local viewer. Summaries live in ~/.config/saga/log-summaries/
cd /home/loraque/.config/saga/log-summaries
exec python3 -m http.server 8765 --bind 127.0.0.1
