#!/bin/zsh
# Double-click this file to open the local website editor.
cd -- "${0:A:h}" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print "Python 3 is needed to run the website editor. Install Python 3 from https://www.python.org/downloads/."
  read "?Press Return to close this window."
  exit 1
fi
python3 tools/manage.py
result=$?
if (( result != 0 )); then
  read "?Press Return to close this window."
fi
exit $result
