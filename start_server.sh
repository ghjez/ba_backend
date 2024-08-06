#!/bin/zsh

# Open a new terminal window and run redis in a docker container
osascript <<EOF
tell application "Terminal"
    do script "docker run -p 6379:6379 -d redis"
end tell
EOF

# Open a new terminal window and start a celery worker
osascript <<EOF
tell application "Terminal"
    do script "cd $(pwd); celery -A project worker -l info"
end tell
EOF

# Open a new terminal window and start the server itself
osascript <<EOF
tell application "Terminal"
    do script "cd $(pwd); python manage.py runserver"
end tell
EOF
