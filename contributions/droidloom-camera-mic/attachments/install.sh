#!/bin/sh
set -eu
cd "$(dirname "$0")"
action=$1
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
lib="$HOME/.local/lib/tb710fu-droidloom-camera"
state="${XDG_STATE_HOME:-$HOME/.local/state}/tb710fu-droidloom-camera"
# Prefixes distinguish the two unit drop-ins with the same basename.
managed="units/droidloom-devices.socket.d/70-tb710fu-camera.conf
units/droidloom.service.d/70-tb710fu-camera.conf
units/droidloom-audio.service.d/70-tb710fu-capture.conf
units/tb710fu-droidloom-camera.socket
units/tb710fu-droidloom-camera.service
lib/camera.py
lib/droidloom-audio"
path_for() {
    case "$1" in
        units/*) printf '%s/%s\n' "$units" "${1#units/}" ;;
        lib/*) printf '%s/%s\n' "$lib" "${1#lib/}" ;;
    esac
}
if [ "$action" = rollback ]; then
    if [ -d "$state/before" ]; then
        for key in $managed; do
            dest=$(path_for "$key")
            if [ -f "$state/before/$key" ]; then
                mkdir -p "$(dirname "$dest")"
                cp -p -- "$state/before/$key" "$dest"
            elif [ -e "$dest" ]; then
                unlink "$dest"
            fi
        done
        systemctl --user daemon-reload
        rm -r -- "$state/before"
    fi
    exit 0
fi
test "$action" = apply
if [ ! -d "$state/before" ]; then
    mkdir -p "$state"
    tmp=$(mktemp -d "$state/save.XXXXXX")
    trap 'rm -r -- "$tmp"' EXIT
    for key in $managed; do
        dest=$(path_for "$key")
        if [ -f "$dest" ]; then
            mkdir -p "$(dirname "$tmp/$key")"
            cp -p -- "$dest" "$tmp/$key"
        fi
    done
    mv -T "$tmp" "$state/before"
    trap - EXIT
fi
install -d "$lib" "$units/droidloom-devices.socket.d" "$units/droidloom.service.d"
install -m 755 camera.py "$lib/camera.py"
# Split the camera out of a shared device socket when the installed unit uses one.
base=$(systemctl --user show droidloom-devices.socket -p FragmentPath --value 2>/dev/null || :)
dropin="$units/droidloom-devices.socket.d/70-tb710fu-camera.conf"
if [ -r "$base" ] && grep -Eq '^[[:space:]]*ListenSequentialPacket[[:space:]]*=.*/camera\.sock[[:space:]]*$' "$base"; then
    other=$(sed -n '/^[[:space:]]*ListenSequentialPacket[[:space:]]*=/p' "$base" | sed '/\/camera\.sock[[:space:]]*$/d')
    printf '[Socket]\nListenSequentialPacket=\n%s\n' "$other" > "$dropin"
elif [ -f "$dropin" ]; then
    unlink "$dropin"
fi
cat > "$units/tb710fu-droidloom-camera.socket" <<'EOF'
[Unit]
Description=TB710FU Android camera endpoint
PartOf=droidloom.service
[Socket]
ListenSequentialPacket=%t/droidloom/camera.sock
SocketMode=0666
DirectoryMode=0700
RemoveOnStop=true
EOF
cat > "$units/tb710fu-droidloom-camera.service" <<'EOF'
[Unit]
Description=TB710FU libcamera bridge for Android
PartOf=droidloom.service
Requires=tb710fu-droidloom-camera.socket
[Service]
ExecStart=/usr/bin/python3 %h/.local/lib/tb710fu-droidloom-camera/camera.py
Environment=LIBCAMERA_LOG_LEVELS=*:ERROR
NoNewPrivileges=true
Restart=on-failure
EOF
cat > "$units/droidloom.service.d/70-tb710fu-camera.conf" <<'EOF'
[Unit]
Requires=tb710fu-droidloom-camera.socket
After=tb710fu-droidloom-camera.socket
EOF
install -d "$units/droidloom-audio.service.d"
install -m 755 droidloom-audio "$lib/droidloom-audio.new"
mv "$lib/droidloom-audio.new" "$lib/droidloom-audio"
cat > "$units/droidloom-audio.service.d/70-tb710fu-capture.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=%h/.local/lib/tb710fu-droidloom-camera/droidloom-audio --socket %t/droidloom/audio.sock
EOF
systemctl --user daemon-reload
