#!/bin/sh
NAME="$1"
if [ -n "$NAME" ] && lxc-info -n "$NAME" >/dev/null 2>&1; then
    STATE=$(lxc-info -n "$NAME" -s 2>/dev/null | awk '{print $2}')
    if [ "$STATE" != "RUNNING" ]; then
        echo "Container '$NAME' is currently $STATE. Starting..."
        lxc-start -n "$NAME" -d
        sleep 1
    fi
    clear
    echo "=========================================================="
    echo "  Connecting interactive root shell to: $NAME"
    echo "=========================================================="
    if lxc-attach -n "$NAME" -- which bash >/dev/null 2>&1; then
        exec lxc-attach -n "$NAME" -- /bin/bash -l
    else
        exec lxc-attach -n "$NAME" -- /bin/sh -l
    fi
else
    clear
    echo "Container '$NAME' is not found."
    echo ""
    echo "Currently available containers:"
    lxc-ls -1
    echo ""
    echo "Press Enter to exit..."
    read -r _
fi
